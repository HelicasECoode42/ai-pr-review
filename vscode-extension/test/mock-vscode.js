// Minimal mock of vscode API for unit testing the extension logic.
"use strict";

const path = require("path");

// --- VS Code API mocks ---

class Uri {
  /**
   * @param {string} scheme
   * @param {string} authority
   * @param {string} p
   * @param {string} query
   * @param {string} fragment
   */
  constructor(scheme, authority, p, query, fragment) {
    this.scheme = scheme;
    this.authority = authority;
    this.path = p;
    this.query = query;
    this.fragment = fragment;
  }

  get fsPath() {
    return this.path;
  }

  /**
   * @param {string} p
   * @returns {Uri}
   */
  static file(p) {
    return new Uri("file", "", p, "", "");
  }

  /**
   * @param {Uri} base
   * @param {...string} segments
   * @returns {Uri}
   */
  static joinPath(base, ...segments) {
    const joined = path.join(base.fsPath, ...segments);
    return Uri.file(joined);
  }
}

class Range {
  /**
   * @param {number} startLine
   * @param {number} startChar
   * @param {number} endLine
   * @param {number} endChar
   */
  constructor(startLine, startChar, endLine, endChar) {
    this.start = { line: startLine, character: startChar };
    this.end = { line: endLine, character: endChar };
  }
}

/**
 * @param {Range} range
 * @param {string} message
 * @param {number} severity
 */
class Diagnostic {
  constructor(range, message, severity) {
    this.range = range;
    this.message = message;
    this.severity = severity;
    this.source = "";
    this.code = "";
  }
}

const DiagnosticSeverity = {
  Error: 0,
  Warning: 1,
  Information: 2,
  Hint: 3,
};

const workspace = {
  workspaceFolders: [{ uri: Uri.file("/workspace/project") }],
};

// Install the mock
const vscodeMock = {
  Uri,
  Range,
  Diagnostic,
  DiagnosticSeverity,
  workspace,
};

// Override require cache so diagnostics.js gets our mock
const Module = require("module");
const originalLoad = Module._load;

Module._load = function (request, parent, isMain) {
  if (request === "vscode") {
    return vscodeMock;
  }
  return originalLoad.apply(this, arguments);
};

module.exports = vscodeMock;
