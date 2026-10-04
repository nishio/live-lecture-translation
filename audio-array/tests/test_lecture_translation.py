"""CPU-only FIFO and adapter tests; no credentials, microphone, or inference."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_translation as translation
import event_insights_cloud as cloud


def line(index, text=None, **changes):
    row = {"id": f"s{index}", "start_seconds": index * 3.0, "end_seconds": index * 3.0 + 2.5,
           "text": text if text is not None else f"This is the source sentence number {index}.",
           "language": "en", "uncertain": False}
    return {**row, **changes}


def response(plan, text="これは原文に対応する日本語訳です。"):
    return {"blocks": [{"text": text, "source_ids": list(group)} for group in plan["groups"]]}


def resign(plan):
    """Model a malformed externally supplied plan, not just a stale checksum."""
    plan["source_hashes"] = {row["id"]: translation._hash(row) for row in plan["source_lines"]}
    plan["source_fingerprint"] = translation._hash(plan["source_lines"])
    plan["plan_fingerprint"] = translation._hash(translation._core(plan))
    return plan


class PlannerTests(unittest.TestCase):
    def test_fifo_backlog_eventually_covers_every_eligible_line_once(self):
        rows = [line(index) for index in range(103)]
        rows[18]["uncertain"] = True
        rows[34]["language"] = "ja"
        rows[51]["text"] = " "
        expected = [row["id"] for row in rows if translation._eligible({**row, "text": row["text"].strip()})]
        covered, actual = set(), []
        while True:
            plan = translation.plan_translation(rows, covered, flush=True)
            if plan is None:
                break
            self.assertTrue(set(plan["target_source_ids"]).isdisjoint(covered))
            self.assertEqual(plan["target_source_ids"], expected[len(actual):len(actual) + len(plan["target_source_ids"])])
            self.assertLessEqual(len(plan["groups"]), 3)
            self.assertLessEqual(plan["selection"]["selected_target_bytes"], 6000)
            actual.extend(plan["target_source_ids"])
            covered.update(plan["target_source_ids"])
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], "s0")  # Old speech outside any recent window remains pending.

    def test_uncertain_japanese_empty_and_covered_rows_split_groups(self):
        for change, covered in [({"uncertain": True}, set()), ({"language": "ja"}, set()),
                                ({"text": ""}, set()), ({}, {"s1"})]:
            with self.subTest(change=change, covered=covered):
                rows = [line(0), line(1, **change), line(2)]
                plan = translation.plan_translation(rows, covered, flush=True)
                self.assertEqual(plan["groups"], [["s0"], ["s2"]])

    def test_overlap_uncertain_line_is_a_boundary_even_without_silence(self):
        rows = [line(0, end_seconds=4), line(1, start_seconds=2, end_seconds=3, uncertain=True),
                line(2, start_seconds=3, end_seconds=5)]
        self.assertEqual(translation.plan_translation(rows, set(), flush=True)["groups"], [["s0"], ["s2"]])

    def test_large_gap_splits_and_45_second_group_limit_holds(self):
        rows = [line(0, start_seconds=0, end_seconds=30), line(1, start_seconds=30, end_seconds=50),
                line(2, start_seconds=56, end_seconds=57)]
        plan = translation.plan_translation(rows, set(), flush=True)
        self.assertEqual(plan["groups"], [["s0"], ["s1"], ["s2"]])

    def test_partial_group_waits_without_flush_but_barrier_closes_it(self):
        self.assertIsNone(translation.plan_translation([line(0)], set()))
        self.assertEqual(translation.plan_translation([line(0)], set(), flush=True)["groups"], [["s0"]])
        rows = [line(0), line(1, uncertain=True), line(2)]
        self.assertEqual(translation.plan_translation(rows, set())["groups"], [["s0"]])
        self.assertEqual(translation.plan_translation([line(i) for i in range(8)], set())["target_source_ids"],
                         [f"s{i}" for i in range(8)])

    def test_oversized_oldest_is_explicit_and_never_skipped(self):
        for row in [line(0, "x" * 2001), line(0, end_seconds=45.01)]:
            with self.subTest(row=row["end_seconds"]):
                with self.assertRaises(translation.OversizedSourceError) as failure:
                    translation.plan_translation([row, line(1, start_seconds=50, end_seconds=52)], set(), flush=True)
                self.assertEqual(failure.exception.source_id, "s0")
        rows = [line(0), line(1, "x" * 2001), line(2)]
        first = translation.plan_translation(rows, set(), flush=True)
        self.assertEqual(first["target_source_ids"], ["s0"])
        self.assertEqual(first["selection"]["blocked_next_source_id"], "s1")
        with self.assertRaises(translation.OversizedSourceError):
            translation.plan_translation(rows, {"s0"}, flush=True)

    def test_utf8_byte_boundaries_are_not_character_limits(self):
        rows = [line(0, "é" * 500), line(1, "é" * 500), line(2, "é" * 500)]
        plan = translation.plan_translation(rows, set(), flush=True)
        self.assertEqual(plan["groups"], [["s0", "s1"], ["s2"]])
        self.assertEqual(plan["selection"]["selected_target_bytes"], 3000)
        exact = translation.plan_translation([line(0, "x" * 2000, end_seconds=45)], set())
        self.assertEqual(exact["groups"], [["s0"]])

    def test_context_is_original_bounded_and_not_coverage(self):
        rows = [line(i, translation_ja="DO NOT COPY", private_note="SECRET") for i in range(60)]
        plan = translation.plan_translation(rows, {row["id"] for row in rows if row["id"] != "s20"}, flush=True)
        self.assertEqual(plan["target_source_ids"], ["s20"])
        self.assertEqual(plan["through_seconds"], rows[-1]["end_seconds"])
        self.assertEqual(plan["target_through_seconds"], rows[20]["end_seconds"])
        for row in plan["source_lines"]:
            self.assertGreaterEqual(row["start_seconds"], rows[20]["start_seconds"] - 45)
            self.assertLessEqual(row["end_seconds"], rows[20]["end_seconds"] + 45)
        request = translation.build_translation_request(plan)
        payload = json.dumps(request["messages"])
        self.assertNotIn("DO NOT COPY", payload)
        self.assertNotIn("SECRET", payload)
        self.assertNotIn("s59", payload)
        self.assertEqual(plan["source_hashes"], {row["id"]: translation._hash(row) for row in plan["source_lines"]})

    def test_dense_context_omission_is_explicit_targets_intact(self):
        rows = [line(i, "A" * 900, start_seconds=i * 0.1, end_seconds=i * 0.1 + 0.1) for i in range(200)]
        covered = {row["id"] for row in rows[1:]}
        plan = translation.plan_translation(rows, covered, flush=True)
        self.assertEqual(plan["target_source_ids"], ["s0"])
        self.assertGreater(len(plan["selection"]["context_omitted_source_ids"]), 0)
        self.assertEqual(plan["selection"]["context_available_lines"],
                         plan["selection"]["context_selected_lines"] + len(plan["selection"]["context_omitted_source_ids"]))
        self.assertLessEqual(translation.build_translation_request(plan)["input_bytes"], translation.MAX_INPUT_BYTES)

    def test_empty_and_already_covered_have_no_plan(self):
        self.assertIsNone(translation.plan_translation([], set(), flush=True))
        self.assertIsNone(translation.plan_translation([line(0)], {"s0"}, flush=True))
        self.assertIsNone(translation.plan_translation([line(0, uncertain=True), line(1, language="ja")], set(), flush=True))

    def test_bad_input_types_order_ids_times_and_limits_fail_explicitly(self):
        bad_inputs = [[line(0), line(0)], [line(1), line(0)], [line(0, start_seconds=True)],
                      [line(0, end_seconds=float("nan"))], [line(0, end_seconds=-1)],
                      [line(0, uncertain="false")], [line(0, text=None)], [line(0, language=42)],
                      [line(0, id=" s0")], ["line"]]
        # line(None) means default text in this helper; set it explicitly.
        bad_inputs[6][0]["text"] = None
        for rows in bad_inputs:
            with self.subTest(rows=rows), self.assertRaises(translation.TranslationInputError):
                translation.plan_translation(rows, set(), flush=True)
        for coverage in ["s0", {"missing"}, {12}]:
            with self.subTest(coverage=coverage), self.assertRaises(translation.TranslationInputError):
                translation.plan_translation([line(0)], coverage, flush=True)
        with self.assertRaises(translation.TranslationInputError):
            translation.plan_translation([line(0)], set(), flush=1)
        with mock.patch.object(translation, "MAX_TRANSCRIPT_LINES", 1), self.assertRaises(translation.TranslationInputError):
            translation.plan_translation([line(0), line(1)], set(), flush=True)
        with mock.patch.object(translation, "MAX_TRANSCRIPT_BYTES", 1), self.assertRaises(translation.TranslationInputError):
            translation.plan_translation([line(0)], set(), flush=True)

    def test_plan_changes_and_resigned_invalid_groups_are_rejected(self):
        original = translation.plan_translation([line(0), line(1), line(2, uncertain=True), line(3)], set(), flush=True)
        mutated = copy.deepcopy(original)
        mutated["source_lines"][0]["text"] += " changed"
        with self.assertRaises(translation.TranslationInputError):
            translation.build_translation_request(mutated)
        for changes in [{"groups": [["s0", "s3"]], "target_source_ids": ["s0", "s3"]},
                        {"groups": [["s1", "s0"], ["s3"]], "target_source_ids": ["s1", "s0", "s3"]},
                        {"groups": [["s0", "s2"]], "target_source_ids": ["s0", "s2"]},
                        {"through_seconds": 1}, {"target_through_seconds": 0}]:
            with self.subTest(changes=changes), self.assertRaises(translation.TranslationInputError):
                translation.build_translation_request(resign({**copy.deepcopy(original), **changes}))

    def test_planning_and_building_are_pure(self):
        with mock.patch.object(cloud, "_api_key", side_effect=AssertionError("key read")), \
                mock.patch.object(cloud, "generate", side_effect=AssertionError("inference")), \
                mock.patch.object(cloud, "_atomic_json", side_effect=AssertionError("write")):
            plan = translation.plan_translation([line(0)], set(), flush=True)
            request = translation.build_translation_request(plan)
            cloud.build_payload(request["messages"], request["schema"], "gpt-6-luna")


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.plan = translation.plan_translation([line(0), line(1), line(2, uncertain=True), line(3)], set(), flush=True)

    def test_exact_ordered_groups_validate_from_object_and_json(self):
        value = response(self.plan)
        self.assertEqual(translation.validate_translation_response(value, self.plan), value)
        self.assertEqual(translation.validate_translation_response(json.dumps(value), self.plan), value)

    def test_missing_extra_reordered_unknown_duplicate_and_english_fail(self):
        good = response(self.plan)
        bad = [{"blocks": []}, {"blocks": good["blocks"][:1]}, {"blocks": list(reversed(good["blocks"]))},
               {"blocks": good["blocks"] + good["blocks"]}, {**good, "summary": []},
               {"blocks": [{"text": "訳", "source_ids": ["missing"]}, good["blocks"][1]]},
               {"blocks": [{"text": "訳", "source_ids": ["s1", "s0"]}, good["blocks"][1]]},
               {"blocks": [{"text": "訳", "source_ids": ["s0", "s0"]}, good["blocks"][1]]},
               response(self.plan, "This is an unchanged English sentence."), response(self.plan, " "),
               response(self.plan, "訳" * (translation.MAX_BLOCK_TEXT + 1)),
               '{"blocks":[],"blocks":[]}']
        for value in bad:
            with self.subTest(value=str(value)[:100]), self.assertRaises(translation.TranslationResponseError):
                translation.validate_translation_response(value, self.plan)

    def test_unchanged_short_name_allowed_but_english_clause_not_allowed(self):
        plan = translation.plan_translation([line(0, "OpenAI")], set(), flush=True)
        self.assertEqual(translation.validate_translation_response(response(plan, "OpenAI"), plan)["blocks"][0]["text"], "OpenAI")
        with self.assertRaises(translation.TranslationResponseError):
            translation.validate_translation_response(response(plan, "Microsoft"), plan)
        plan = translation.plan_translation([line(0, "We Are Here")], set(), flush=True)
        with self.assertRaises(translation.TranslationResponseError):
            translation.validate_translation_response(response(plan, "We Are Here"), plan)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.plan = translation.plan_translation([line(0), line(1)], set(), flush=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        roots = mock.patch.object(translation, "PRIVATE_ROOTS", (Path(self.tmp.name),))
        roots.start()
        self.addCleanup(roots.stop)
        no_key = mock.patch.object(cloud, "_api_key", side_effect=AssertionError("test must not read keys"))
        no_key.start()
        self.addCleanup(no_key.stop)

    def call(self, **overrides):
        return translation.translate_batch(self.plan, **{"provider": "openai", "model": "gpt-6-luna",
            "out_dir": self.tmp.name, **overrides})

    def fake_generate(self, messages, schema, **options):
        options["observe_response"]({"id": "fake-response", "output": []})
        return {"result": options["validate"](response(self.plan)), "usage": {"input_tokens": 100, "output_tokens": 50},
                "cost_usd": 0.002, "usage_confirmed": True, "cache_hit": False, "spent_usd": 0.2}

    def test_single_call_forwards_timeout_manual_retry_and_saves_evidence(self):
        with mock.patch.object(cloud, "generate", side_effect=self.fake_generate) as generate:
            result = self.call(timeout=37, retry_failed=True)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs["timeout"], 37)
        self.assertIs(generate.call_args.kwargs["retry_failed"], True)
        self.assertEqual(result["target_source_ids"], ["s0", "s1"])
        self.assertEqual(result["source_hashes"], self.plan["source_hashes"])
        self.assertFalse(result["semantic_quality_verified"])
        directory = Path(result["artifact_dir"])
        self.assertEqual({path.name for path in directory.iterdir()},
                         {"request.json", "payload.json", "response.json", "generated.json", "result.json"})
        saved = json.loads((directory / "request.json").read_text())
        self.assertEqual(saved["plan_fingerprint"], self.plan["plan_fingerprint"])
        self.assertEqual(json.loads((directory / "payload.json").read_text())["store"], False)

    def test_default_no_retry_timeout_is_not_retried_and_retains_error_request(self):
        with mock.patch.object(cloud, "generate", side_effect=TimeoutError("fake timeout")) as generate:
            with self.assertRaises(TimeoutError):
                self.call()
        generate.assert_called_once()
        self.assertIs(generate.call_args.kwargs["retry_failed"], False)
        directory = next(Path(self.tmp.name).iterdir())
        self.assertTrue((directory / "payload.json").is_file())
        self.assertTrue((directory / "error.json").is_file())
        self.assertFalse((directory / "result.json").exists())

    def test_invalid_response_preserves_raw_and_never_returns_partial_coverage(self):
        def bad_generate(messages, schema, **options):
            options["observe_response"]({"raw": "invalid-output"})
            return {"result": {"blocks": []}}
        with mock.patch.object(cloud, "generate", side_effect=bad_generate):
            with self.assertRaises(translation.TranslationResponseError):
                self.call()
        directory = next(Path(self.tmp.name).iterdir())
        self.assertEqual(json.loads((directory / "response.json").read_text()), {"raw": "invalid-output"})
        self.assertFalse((directory / "result.json").exists())

    def test_cache_hit_charges_zero_and_each_attempt_keeps_its_directory(self):
        generated = {"result": response(self.plan), "cost_usd": 0.02, "cache_hit": True, "usage_confirmed": True}
        with mock.patch.object(cloud, "generate", return_value=generated):
            first, second = self.call(), self.call()
        self.assertEqual(first["cost_usd"], 0)
        self.assertEqual(first["cached_request_cost_usd"], 0.02)
        self.assertNotEqual(first["artifact_dir"], second["artifact_dir"])

    def test_caller_plan_mutation_during_request_cannot_change_saved_coverage(self):
        before = copy.deepcopy(self.plan)
        def mutate_generate(messages, schema, **options):
            self.plan["source_lines"][0]["text"] = "mutated during request"
            self.plan["groups"].clear()
            return {"result": options["validate"](response(before)), "cost_usd": 0.001}
        with mock.patch.object(cloud, "generate", side_effect=mutate_generate):
            result = self.call()
        self.assertEqual(result["source_hashes"], before["source_hashes"])
        self.assertEqual(result["groups"], before["groups"])

    def test_bad_config_fails_before_generation_or_output_creation(self):
        for kwargs in [{"provider": "local"}, {"timeout": 0}, {"timeout": True}, {"retry_failed": 1},
                       {"model": "not-priced"}, {"model": " gpt-6-luna"}]:
            with self.subTest(kwargs=kwargs), mock.patch.object(cloud, "generate") as generate:
                with self.assertRaises(translation.TranslationInputError):
                    self.call(**kwargs)
                generate.assert_not_called()
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_storage_failure_after_generation_propagates_without_success(self):
        original_save = translation._save
        def failing_save(directory, name, value):
            if name == "result.json":
                raise OSError("fake disk full")
            return original_save(directory, name, value)
        with mock.patch.object(cloud, "generate", side_effect=self.fake_generate) as generate, \
                mock.patch.object(translation, "_save", side_effect=failing_save):
            with self.assertRaises(OSError):
                self.call()
        generate.assert_called_once()
        directory = next(Path(self.tmp.name).iterdir())
        self.assertTrue((directory / "generated.json").exists())
        self.assertTrue((directory / "error.json").exists())

    def test_real_adapter_keeps_failed_reservation_and_requires_manual_new_attempt(self):
        # Exercise the actual persistent ledger/cache with only its HTTP/key
        # boundary replaced. This test never reaches the real shared ledger.
        state_dir = Path(self.tmp.name) / "isolated-ledger"
        fresh_response = {"model": "gpt-6-luna", "service_tier": "default", "status": "completed",
            "output": [{"type": "message", "status": "completed", "content": [{"type": "output_text",
                "text": json.dumps(response(self.plan))}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50,
                      "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}}
        with mock.patch.object(cloud, "STATE_DIR", state_dir), \
                mock.patch.object(cloud, "_api_key", return_value="fake-key-never-sent"), \
                mock.patch.object(cloud, "_day", return_value="2030-06-04"), \
                mock.patch.object(cloud, "_budget", return_value=cloud.NANODOLLARS), \
                mock.patch.object(cloud, "_post", side_effect=[TimeoutError("ambiguous"), fresh_response]) as post:
            with self.assertRaises(TimeoutError):
                self.call()
            first = cloud._load_ledger()
            entry = next(iter(first["requests"].values()))
            held = entry["charged_nanodollars"]
            self.assertEqual(entry["state"], "failed")
            self.assertEqual(held, entry["reserved_nanodollars"])
            with self.assertRaises(cloud.CloudError):
                self.call()
            self.assertEqual(post.call_count, 1)
            result = self.call(retry_failed=True)
            self.assertEqual(post.call_count, 2)
            ledger = cloud._load_ledger()
            self.assertEqual(len(ledger["requests"]), 2)
            self.assertEqual(sum(item["state"] == "failed" for item in ledger["requests"].values()), 1)
            self.assertGreater(result["spent_usd"], held / cloud.NANODOLLARS)
            self.assertEqual(result["reserved_usd"], held / cloud.NANODOLLARS)
            cached = self.call()
            self.assertEqual(post.call_count, 2)
            self.assertEqual(cached["cost_usd"], 0)
            self.assertTrue(cached["cache_hit"])


if __name__ == "__main__":
    unittest.main()
