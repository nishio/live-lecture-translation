# Audrey Tangの講演から生成したサンプル

公開講演の音声を使い、このアプリで実際に生成した英語の文字起こし、日本語訳、理解支援を収めています。人間による校正は行っていません。精度の正解例や、話者が承認した翻訳・要約ではありません。

## 出典と帰属

- 元動画：[Audrey Tang Remarks in Plurality Seoul, 2023 12 02（字幕あり）](https://www.youtube.com/watch?v=4_tge6XJhGA)
- 投稿者：Code for Japan。公開日：2023年12月26日。
- 話者：Audrey Tang。内容：Plurality、デジタル民主主義、AIを使った熟議などの説明。
- 元動画のライセンス表示：**CC BY（Creative Commons Attribution license, reuse allowed）**。保存したYouTubeメタデータに版番号は記載されていません。
- 改変：音声を16 kHz・モノラル・16 bit PCM WAVへ変換し、ASRで文字起こし、その認識結果からAIによる日本語訳、要約、話の流れ、概念説明を生成しました。公開用に出力項目を選び、JSONとMarkdownに整形しています。

[YouTube公式ヘルプ](https://support.google.com/youtube/answer/2797468?hl=en)は、現在[CC BY 4.0の説明](https://creativecommons.org/licenses/by/4.0/)へリンクしています。これはライセンスの参考資料であり、元動画メタデータにない版番号を補って断定するものではありません。別の[公開告知](https://scrapbox.io/plurality-japanese/雑談ページ2)にあるCC0表記とは区別し、本サンプルの元動画への帰属にはCC BYを明記しています。

この帰属表示はサンプルの元資料に関するものです。アプリのソースコードを含むリポジトリ全体のライセンスは未選定であり、元動画のCC BY表示がコード全体へ適用されるわけではありません。

## 内容を読む

| ファイル | 内容 |
| --- | --- |
| [transcript.md](transcript.md) / [transcript.json](transcript.json) | 音声から認識した英語の原文。時刻、source ID、不確実性を保持しています。 |
| [translation.md](translation.md) / [translation.json](translation.json) | 認識結果に基づく日本語訳。参照先は原文のsource IDです。 |
| [analyses.md](analyses.md) / [analyses.json](analyses.json) | 各時点の要約、話の流れ、概念説明など、理解を助けるためのAI出力。 |
| [provenance.json](provenance.json) | 元動画、帰属、入力音声の形式・長さ・ハッシュ、生成条件の公開用記録。 |
| [measurements.json](measurements.json) | 処理の状態、件数、時間、API費用などの公開用計測値。 |

元の音声・動画、投稿者が配布した字幕、APIペイロード・生レスポンス・キャッシュ、認証情報、ローカルパスを含む実行記録は含めていません。音声から再実行する場合は[取得・変換と実験の手順](../../docs/audio-experiments.md#単独講演の入力例)を参照してください。サンプルの閲覧には録音・推論・API通信は不要です。

## 評価するときの注意

文字起こしは正解の字幕ではありません。固有名詞、文の境界、聞き取りにくい部分などに誤りや欠落があり得ます。このサンプルを生成した版では、`uncertain` の行を不確実な認識として残し、連続翻訳の対象から除外しました。そのため、翻訳対象のsource IDをすべて処理していても、発話全体を正しく訳したことにはなりません。

要約・主旨・話の関係・概念説明はAIによる解釈です。概念の `lecture` は原文に基づく説明、`background` はモデルの一般知識による補足を区別します。後者のsource IDは語が登場した箇所を示し、補足内容そのものの根拠ではありません。検索による裏取りは行っていません。原文と元動画を照合して内容を評価してください。

処理の完了、対象行の網羅、source IDの整合性は、認識や意味の正確さを保証しません。この保存音声による試験は、会場でのマイク収録、発話から画面までの遅延、聴講中の理解や注意負担を測定したものでもありません。

費用は[measurements.json](measurements.json)の実測値を確認してください。usageに基づく確定API料金と、失敗などによる未確定の保持予約は別に扱います。過去の時間比例の見積もりや、同じ音声を将来再実行したときの料金保証ではありません。開発用AIの利用と電力は未計測です。
