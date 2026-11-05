const fs = require("node:fs");
const vm = require("node:vm");

const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const reportNode = { innerHTML: "", textContent: "", style: {} };
const nodes = new Map();
function makeNode(id) {
  if (!nodes.has(id)) {
    nodes.set(id, {
      innerHTML: "",
      textContent: "",
      value: id === "tl-repo" ? "owner/repo" : id === "tl-count" ? "10" : "",
      disabled: false,
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener() {},
    });
  }
  return nodes.get(id);
}
const document = {
  baseURI: "https://review.example/",
  addEventListener() {},
  getElementById(id) {
    return id === "report-html" ? reportNode : makeNode(id);
  },
  createElement() {
    return {
      innerHTML: "",
      set textContent(value) {
        this.innerHTML = String(value).replace(/[&<>"']/g, char => ({
          "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[char]);
      },
    };
  },
};
const context = vm.createContext({
  document,
  URL,
  console,
  fetch: async (url) => ({
    ok: true,
    json: async () => payload.action === "history"
      ? payload.response
      : { prs: payload.response },
  }),
});

if (payload.useMarked) {
  vm.runInContext(fs.readFileSync(payload.markedPath, "utf8"), context, { filename: "marked.umd.js" });
}
vm.runInContext(fs.readFileSync(payload.appPath, "utf8"), context, { filename: "app.js" });
(async () => {
  let output;
  if (payload.action === "history") {
    await vm.runInContext("loadHistory()", context);
    output = {
      html: nodes.get("history-list").innerHTML,
      summary: nodes.get("history-summary").innerHTML,
    };
  } else if (payload.action === "timeline") {
    await vm.runInContext("loadTimeline()", context);
    output = {
      html: nodes.get("timeline-list").innerHTML,
      summary: nodes.get("timeline-summary").innerHTML,
    };
  } else {
    vm.runInContext(`renderMarkdown(${JSON.stringify(payload.markdown)})`, context);
    output = { html: reportNode.innerHTML, text: reportNode.textContent };
  }
  process.stdout.write(JSON.stringify(output));
})().catch(error => {
  process.stderr.write(`${error.stack || error}`);
  process.exitCode = 1;
});
