'use strict';
// Shared synthetic DOM for the reading renderer and playback-controller integration.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this._text = ''; this.value = '';
    this.hidden = false; this.disabled = false; this.dataset = {}; this.style = {setProperty(name, value) {this[name] = value;}}; this.attributes = {};
    this.handlers = {}; this.scrollTop = 0; this.scrollHeight = 300; this.clientHeight = 240;
    this.rect = {top: 100, height: 400}; this.capturedPointers = new Set();
    this.classes = new Set(); this.classList = {toggle: (name, value) => value ? this.classes.add(name) : this.classes.delete(name)};
  }
  set textContent(value) { for (const child of this.children) child.parentNode = null; this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Untrusted content must not be rendered as HTML'); }
  appendChild(child) { child.parentNode?.removeChild(child); this.children.push(child); child.parentNode = this; return child; }
  insertBefore(child, before) {
    if (child === before) return child;
    child.parentNode?.removeChild(child);
    const index = before ? this.children.indexOf(before) : this.children.length;
    this.children.splice(index, 0, child); child.parentNode = this; return child;
  }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; return child; }
  replaceChildren(...children) { this.textContent = ''; for (const child of children) this.appendChild(child); }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  getBoundingClientRect() { return this.rect; }
  setPointerCapture(id) { this.capturedPointers.add(id); }
  hasPointerCapture(id) { return this.capturedPointers.has(id); }
  releasePointerCapture(id) { this.capturedPointers.delete(id); }
  click() { return this.handlers.click?.(); }
  scrollIntoView() { this.scrolled = true; }
  focus() { this.focused = true; if (this.ownerDocument) this.ownerDocument.activeElement = this; }
}
function fixtureDOM(html = fs.readFileSync(path.join(__dirname, '../lecture-dashboard/index.html'), 'utf8')) {
  const nodes = new Map([...html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"/g)].map(match => [match[2], new Element(match[1])]));
  nodes.get('language-select').value = 'auto'; nodes.get('provider-select').value = 'local';
  const doc = {nodes, activeElement: null, getElementById: id => { assert(nodes.has(id), `Unknown DOM element: ${id}`); return nodes.get(id); }, createElement: tag => { const node = new Element(tag); node.ownerDocument = doc; return node; }};
  for (const node of nodes.values()) node.ownerDocument = doc;
  return doc;
}

module.exports = {Element, fixtureDOM};
