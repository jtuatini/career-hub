// E2E for the extension's snapshot + executor primitives against realistic
// ATS widgets (react-select-style comboboxes, Lever-style orphan labels,
// radio groups). Runs the REAL content.js in a page — no backend needed.
//
// Run: NODE_PATH=<dir containing playwright> node e2e_real_widgets.cjs
const assert = require("assert");
const path = require("path");
const { chromium } = require("playwright");

const EXT = path.resolve(__dirname, "../../../../extension");
const FIXTURE = "file://" + path.resolve(__dirname, "real_widgets.html");
const PDF_B64 = Buffer.from("%PDF-1.4\n%%EOF\n").toString("base64");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(FIXTURE);

  // ---- negative control: prove the fixture models the real failure --------
  // Raw native-setter writes (the OLD "fill" path) must NOT stick on the
  // react-select-style widget; if this ever starts passing, the fixture has
  // stopped guarding the bug.
  const rawWriteStuck = await page.evaluate(async () => {
    const el = document.getElementById("school-input");
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(el, "Typed University");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.focus(); el.blur();
    await new Promise((r) => setTimeout(r, 400));
    return { visible: el.value, committed: window["__combo_school-input"].committed };
  });
  assert.strictEqual(rawWriteStuck.committed, "", "fixture must reject raw writes");
  assert.strictEqual(rawWriteStuck.visible, "", "fixture must reset visible value on blur");

  // ---- load the real extension code with a chrome stub --------------------
  await page.evaluate((pdfB64) => {
    window.__messages = [];
    window.chrome = {
      runtime: {
        sendMessage: (msg) => {
          window.__messages.push(msg);
          if (msg && msg.type === "fetch_pdf") return Promise.resolve({ b64: pdfB64 });
          return Promise.resolve();
        },
        onMessage: { addListener: () => {} },
      },
    };
  }, PDF_B64);
  await page.addScriptTag({ path: path.join(EXT, "submit_denylist.js") });
  await page.addScriptTag({ path: path.join(EXT, "content.js") });

  // ---- snapshot classification -------------------------------------------
  const snap = await page.evaluate(() => __snapshot());
  const byLabel = (needle) => snap.fields.find((f) => (f.label || "").includes(needle));

  const school = byLabel("School");
  assert.strictEqual(school.type, "combobox", "input[role=combobox] must snapshot as combobox, not text");
  const cpp = byLabel("C++");
  assert.strictEqual(cpp.type, "combobox", "wrapper-role combobox (react-select v4 shape) must classify too");
  assert.strictEqual(
    snap.fields.filter((f) => f.type === "combobox").length, 2,
    "one widget = one field: the [role=combobox] wrapper must not be captured twice",
  );
  const gpa = byLabel("GPA");
  assert.strictEqual(gpa.type, "text");
  const essay = snap.fields.find((f) => f.type === "textarea");
  assert.ok(essay.label.includes("Why do you want to work at TestCorp"),
    `Lever-style textarea label must be extracted, got ${JSON.stringify(essay.label)}`);
  const radios = snap.fields.filter((f) => f.type === "radio");
  assert.strictEqual(radios.length, 2);
  for (const r of radios) {
    assert.ok(r.group_label.includes("legally authorized"),
      `radio group_label must carry the question, got ${JSON.stringify(r.group_label)}`);
  }
  const yes = radios.find((r) => r.label === "Yes");
  const file = snap.fields.find((f) => f.type === "file");
  assert.ok(file, "file input snapshotted");
  assert.ok(snap.buttons.some((b) => b.text === "Submit Application"));

  // ---- execute a plan shaped like build_plan's output ---------------------
  const actions = [
    { kind: "combobox", index: school.index, value: "University of Michigan - Ann Arbor", review: true, label: school.label },
    { kind: "combobox", index: cpp.index, value: "3+ years", review: true, label: cpp.label },
    { kind: "fill", index: gpa.index, value: "3.86", review: false, label: gpa.label },
    { kind: "fill", index: essay.index, value: "Because I build flight software.", review: true, essay: true, label: essay.label },
    { kind: "check", index: yes.index, value: "Yes", review: true, label: yes.group_label },
    { kind: "attach", index: file.index, doc_kind: "resume", doc_id: 1, filename: "Sample_Alex_resume.pdf", review: true, label: file.label },
  ];
  await page.evaluate((a) => __execute(a), actions);

  const state = await page.evaluate(() => ({
    school: window["__combo_school-input"].committed,
    cpp: window["__combo_cpp-input"].committed,
    gpa: document.getElementById("gpa").value,
    essay: document.querySelector("textarea").value,
    yesChecked: document.querySelector('input[value="yes"]').checked,
    fileName: document.getElementById("resume").files[0]?.name ?? null,
    submitted: window.__submitted,
    report: window.__messages.find((m) => m.type === "report"),
  }));

  assert.strictEqual(state.school, "University of Michigan - Ann Arbor", "school combobox must COMMIT via option pick");
  assert.strictEqual(state.cpp, "3+ years", "static combobox must commit");
  assert.strictEqual(state.gpa, "3.86");
  assert.strictEqual(state.essay, "Because I build flight software.");
  assert.strictEqual(state.yesChecked, true, "radio group answer must be checked");
  assert.strictEqual(state.fileName, "Sample_Alex_resume.pdf", "resume must upload under the personalized filename");
  assert.strictEqual(state.submitted, false, "NEVER submits");
  const statuses = state.report.payload.results.map((r) => r.status);
  assert.ok(statuses.every((s) => s === "filled" || s === "review"), `all actions must land: ${statuses}`);

  await browser.close();
  console.log("E2E REAL-WIDGETS: ALL ASSERTIONS PASSED");
})().catch((e) => { console.error(e); process.exit(1); });
