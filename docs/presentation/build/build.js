// 3-minute pitch: "Spec in. Strategy out." — 7 slides, 16:9 wide.
const path = require("path");
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || "pitch.pptx";
const SKILL = "C:/Users/elias/.claude/skills/synced/bd94ca3a-fb24-43b4-841d-af9e3416dc5c_a932bba9-0b58-423b-80e8-91aec2514b27/pptx";

const INK = "0F1218", INK2 = "1B2029", LINE_D = "2A313C", NODE = "3A4350";
const PAPER = "F5F6F8", CARD = "FFFFFF", LINE_L = "D9DDE3";
const TEXT_L = "F3F0E8", MUTED_D = "AEB6C2", FAINT_D = "7D8694";
const TEXT_D = "14171C", MUTED_L = "4A5260";
const ORANGE = "FF7A33", ORANGE_DK = "B44A12", BLUE = "7FAEFF", BLUE_DK = "2F5FC4", GREY_BAR = "9AA3B2";
const HEAD = "Century Gothic", BODY = "Calibri", MONO = "Consolas";

const THEME = {
  name: "Spec to Strategy", headFontFace: HEAD, bodyFontFace: BODY,
  colors: { dk1: INK, lt1: CARD, dk2: TEXT_D, lt2: PAPER, accent1: ORANGE, accent2: BLUE_DK,
            accent3: GREY_BAR, accent4: ORANGE_DK, accent5: BLUE, accent6: MUTED_L,
            hlink: BLUE_DK, folHlink: MUTED_L },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 in
pres.title = "Spec in. Strategy out.";
pres.subject = "Zentral Hack 2026: an agent harness for unseen games";
pres.theme = { headFontFace: HEAD, bodyFontFace: BODY };

const W = 13.333, M = 0.6;
function master(name, bg, titleColor, numColor) {
  pres.defineSlideMaster({
    title: name,
    background: { color: bg },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: M, y: 0.78, w: W - 2 * M, h: 0.9,
        fontFace: HEAD, fontSize: 34, bold: true, color: titleColor, margin: 0, valign: "top", align: "left" },
        text: "" } },
    ],
    slideNumber: { x: W - M - 0.6, y: 6.95, w: 0.6, h: 0.3, fontFace: MONO, fontSize: 11, color: numColor, align: "right" },
  });
}
master("DARK", INK, TEXT_L, FAINT_D);
master("LIGHT", PAPER, TEXT_D, "6B7380");
master("ACCENT", ORANGE, INK, "3A2010");
pres.defineSlideMaster({ title: "COVER", background: { color: INK }, objects: [] });

function eyebrow(slide, text, color) {
  slide.addText(text, { x: M, y: 0.42, w: 8, h: 0.3, fontFace: MONO, fontSize: 12, color, bold: true,
    charSpacing: 3, margin: 0, isTextBox: true, objectName: "eyebrow" });
}
function shadow() { return { type: "outer", color: "000000", opacity: 0.08, blur: 8, offset: 2, angle: 90 }; }
function line(slide, x1, y1, x2, y2, color, wPt) {
  slide.addShape(pres.shapes.LINE, { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1) || 0.001,
    h: Math.abs(y2 - y1) || 0.001, flipV: (x2 - x1) * (y2 - y1) < 0, line: { color, width: wPt } });
}

// ---------------------------------------------------------------- 1 cover
pres.addSection({ title: "Why" });
{
  const s = pres.addSlide({ masterName: "COVER", sectionTitle: "Why" });
  // motif: a game map, one path from base to base highlighted
  const ox = 8.0, oy = 1.45, k = 0.0062; // 760-unit drawing scaled into ~4.7 in
  const P = (x, y) => [ox + x * k, oy + y * k];
  const nodes = { A: [90, 380], B: [250, 200], C: [250, 560], D: [420, 110], E: [420, 380], F: [420, 650], G: [590, 240], H: [590, 520], I: [690, 380] };
  const edges = ["AB", "AC", "BD", "BE", "CE", "CF", "DG", "EG", "EH", "FH", "GI", "HI", "DE"];
  const path_ = ["AB", "BE", "EH", "HI"];
  for (const e of edges) { const [a, b] = [P(...nodes[e[0]]), P(...nodes[e[1]])]; line(s, a[0], a[1], b[0], b[1], LINE_D, 1.5); }
  for (const e of path_) { const [a, b] = [P(...nodes[e[0]]), P(...nodes[e[1]])]; line(s, a[0], a[1], b[0], b[1], ORANGE, 3.5); }
  for (const [id, [x, y]] of Object.entries(nodes)) {
    const [cx, cy] = P(x, y);
    const base = id === "A" || id === "I", hot = "BEH".includes(id);
    const r = base ? 0.17 : 0.1;
    s.addShape(pres.shapes.OVAL, { x: cx - r, y: cy - r, w: 2 * r, h: 2 * r,
      fill: { color: base ? INK : hot ? ORANGE : INK2 },
      line: { color: base ? (id === "A" ? ORANGE : BLUE) : hot ? ORANGE : NODE, width: base ? 3 : 1.5 },
      objectName: `node ${id}` });
  }
  s.addText("ZENTRAL HACK 2026 · AGENT HARNESS", { x: M, y: 0.7, w: 7, h: 0.35, fontFace: MONO, fontSize: 13,
    bold: true, color: ORANGE, charSpacing: 3, margin: 0, isTextBox: true });
  s.addText("Spec in.\nStrategy out.", { x: M, y: 2.0, w: 7.2, h: 2.4, fontFace: HEAD, fontSize: 60, bold: true,
    color: TEXT_L, margin: 0, valign: "top", lineSpacingMultiple: 0.95, isTextBox: true, objectName: "title" });
  s.addText("An agent harness for games it has never seen.",
    { x: M, y: 4.55, w: 6.6, h: 0.6, fontFace: BODY, fontSize: 20, color: MUTED_D, margin: 0, valign: "top", isTextBox: true });
  s.addText("Elias  ·  Isaak  ·  Timo", { x: M, y: 6.6, w: 6, h: 0.45, fontFace: HEAD, fontSize: 18, bold: true, color: MUTED_D, margin: 0, isTextBox: true });
  s.addNotes("(0:00–0:15) Hi, we are Elias, Isaak and Timo. Our harness gets a game it has never seen, only as a written spec, and has to hand back a strategy that wins. Alone, in a sandbox, on a fixed budget. In three minutes: the idea, how we built it, and what we measured.");
}

// ---------------------------------------------------------------- 2 insight
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "Why" });
  eyebrow(s, "THE TASK · THE INSIGHT", ORANGE_DK);
  s.addText("An unseen game, a fixed budget, only wins count", { placeholder: "title" });
  const y = 2.3, h = 3.75, w = 3.85, gap = 0.3;
  const xs = [M, M + w + gap, M + 2 * (w + gap)];
  // card 1: the task
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: xs[0], y, w, h, rectRadius: 0.12, fill: { color: CARD }, line: { color: LINE_L, width: 0.75 }, shadow: shadow(), objectName: "card task" });
  s.addText("The task", { x: xs[0] + 0.35, y: y + 0.3, w: w - 0.7, h: 0.4, fontFace: HEAD, fontSize: 20, bold: true, color: TEXT_D, margin: 0, isTextBox: true });
  const tiers = [["A", "$0.03", "3 min"], ["B", "$0.50", "10 min"], ["C", "$10", "30 min"]];
  tiers.forEach(([t, usd, min], i) => {
    const ty = y + 1.05 + i * 0.72;
    s.addText(t, { x: xs[0] + 0.35, y: ty, w: 0.45, h: 0.4, fontFace: MONO, fontSize: 20, bold: true, color: ORANGE_DK, margin: 0, isTextBox: true });
    s.addText(`${usd}  ·  ${min}`, { x: xs[0] + 0.85, y: ty, w: w - 1.2, h: 0.45, fontFace: BODY, fontSize: 22, bold: true, color: TEXT_D, margin: 0, isTextBox: true });
  });
  // card 2: research
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: xs[1], y, w, h, rectRadius: 0.12, fill: { color: CARD }, line: { color: LINE_L, width: 0.75 }, shadow: shadow(), objectName: "card research" });
  s.addText("What research says", { x: xs[1] + 0.35, y: y + 0.3, w: w - 0.7, h: 0.4, fontFace: HEAD, fontSize: 20, bold: true, color: TEXT_D, margin: 0, isTextBox: true });
  s.addText([{ text: "9", options: { color: BLUE_DK } }, { text: " / 10", options: { color: GREY_BAR } }],
    { x: xs[1] + 0.35, y: y + 0.95, w: w - 0.7, h: 0.9, fontFace: HEAD, fontSize: 44, bold: true, margin: 0, isTextBox: true });
  s.addText("games where code written by an LLM matches or beats an LLM picking the moves", { x: xs[1] + 0.35, y: y + 1.95, w: w - 0.7, h: 0.75, fontFace: BODY, fontSize: 16, color: MUTED_L, margin: 0, valign: "top", isTextBox: true });
  s.addText("Code World Models, Google DeepMind", { x: xs[1] + 0.35, y: y + 2.95, w: w - 0.7, h: 0.5, fontFace: BODY, fontSize: 14, italic: true, color: MUTED_L, margin: 0, valign: "top", isTextBox: true });
  // card 3: insight (dark)
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: xs[2], y, w, h, rectRadius: 0.12, fill: { color: INK }, line: { color: INK, width: 0.75 }, shadow: shadow(), objectName: "card insight" });
  s.addText("Our insight", { x: xs[2] + 0.35, y: y + 0.3, w: w - 0.7, h: 0.4, fontFace: HEAD, fontSize: 20, bold: true, color: ORANGE, margin: 0, isTextBox: true });
  s.addText("Plain code, not the LLM, checks everything it writes.", { x: xs[2] + 0.35, y: y + 2.7, w: w - 0.7, h: 0.8, fontFace: BODY, fontSize: 18, bold: false, color: MUTED_D, margin: 0, valign: "top", isTextBox: true });
  s.addText("The LLM is the programmer, not the player.", { x: xs[2] + 0.35, y: y + 0.95, w: w - 0.7, h: 1.45, fontFace: HEAD, fontSize: 30, bold: true, color: TEXT_L, margin: 0, valign: "top", isTextBox: true });
  s.addText("Sources: Zentral Hack 2026 task description; Code World Models for General Game Playing, arXiv 2510.04542.",
    { x: M, y: 6.92, w: 10.5, h: 0.32, fontFace: BODY, fontSize: 10, color: "6B7380", margin: 0, isTextBox: true });
  s.addNotes("(0:15–0:45) The task: a written game spec in, a strategy file out, unattended, on three budgets from three cents and three minutes up to ten dollars and thirty minutes. Only wins count. We started with research: Google DeepMind’s Code World Models: an LLM that writes a simulator plus search code matches or beats Gemini 2.5 Pro playing directly in 9 of 10 games. And our strategy may not call an LLM during the game anyway: stdlib only, no network. So the LLM is the programmer, not the player, and it never checks its own work: plain code runs everything it writes and keeps only what is proven.");
}

// ---------------------------------------------------------------- 3 pipeline
pres.addSection({ title: "How" });
{
  const s = pres.addSlide({ masterName: "DARK", sectionTitle: "How" });
  eyebrow(s, "THE HARNESS", ORANGE);
  s.addText("The LLM writes, our code verifies", { placeholder: "title" });
  const stages = [
    ["1", "Write", "drafts + a simulator"],
    ["2", "Verify", "against the spec"],
    ["3", "Self-play", "real tournament rules"],
    ["4", "Improve", "challengers vs. champion"],
    ["5", "Guard", "never a forfeit"],
  ];
  const y = 2.95, h = 2.0, w = 2.12, arrow = 0.39;
  stages.forEach(([n, name, desc], i) => {
    const x = M + i * (w + arrow);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.1, fill: { color: INK2 }, line: { color: i === 4 ? ORANGE : NODE, width: i === 4 ? 1.5 : 0.75 }, objectName: `stage ${name}` });
    s.addText(n, { x: x + 0.2, y: y + 0.22, w: 0.5, h: 0.35, fontFace: MONO, fontSize: 14, bold: true, color: ORANGE, margin: 0, isTextBox: true });
    s.addText(name, { x: x + 0.2, y: y + 0.62, w: w - 0.4, h: 0.45, fontFace: HEAD, fontSize: 20, bold: true, color: TEXT_L, margin: 0, isTextBox: true });
    s.addText(desc, { x: x + 0.2, y: y + 1.2, w: w - 0.35, h: 0.7, fontFace: BODY, fontSize: 16, color: MUTED_D, margin: 0, valign: "top", isTextBox: true });
    if (i < 4) s.addShape(pres.shapes.RIGHT_ARROW, { x: x + w + 0.08, y: y + h / 2 - 0.12, w: arrow - 0.16, h: 0.24, fill: { color: NODE }, line: { color: NODE, width: 0 }, objectName: "arrow" });
  });
  // the loop between self-play and improve
  const x3 = M + 2 * (w + arrow), x4 = M + 3 * (w + arrow);
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x3 + 0.2, y: y + h + 0.22, w: x4 + w - x3 - 0.4, h: 0.42, rectRadius: 0.21, fill: { color: INK }, line: { color: ORANGE, width: 1 }, objectName: "loop" });
  s.addText("↻  repeat while time and money last", { x: x3 + 0.2, y: y + h + 0.22, w: x4 + w - x3 - 0.4, h: 0.42, fontFace: BODY, fontSize: 14, color: ORANGE, align: "center", valign: "middle", margin: 0, isTextBox: true });
  s.addText("spec.md in", { x: M, y: 2.52, w: 2.5, h: 0.3, fontFace: MONO, fontSize: 12, color: FAINT_D, margin: 0, isTextBox: true });
  s.addText("strategy.py out", { x: W - M - 2.5, y: 2.52, w: 2.5, h: 0.3, fontFace: MONO, fontSize: 12, color: FAINT_D, align: "right", margin: 0, isTextBox: true });
  const facts = [];
  facts.forEach(([k, v], i) => {
    const x = M + i * 6.2;
    s.addText([{ text: k + "  ", options: { bold: true, color: TEXT_L } }, { text: v, options: { color: MUTED_D } }],
      { x, y: 5.85, w: 5.9, h: 0.65, fontFace: BODY, fontSize: 14, margin: 0, valign: "top", isTextBox: true });
  });
  s.addNotes("(0:45–1:30) How: the LLM writes, our code verifies. The LLM writes several strategy drafts with different strategic personas and, in parallel, a simulator of the game. Nothing is trusted: the simulator must reproduce the spec’s worked examples, every draft is probed and repaired from its tracebacks. The survivors play a round robin on the simulator, both seats, many seeds, under the tournament’s real time limits. Then the strongest model writes challengers against the champion’s weak spots, and we repeat while time and money last. Around all of it, a guard: hard timer, fallback strategy, output check, so a forfeit is impossible by construction. Budget and deadline are enforced per call.");
}

// ---------------------------------------------------------------- 4 loop
{
  const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: "How" });
  eyebrow(s, "HOW WE BUILT IT", ORANGE_DK);
  s.addText("Built like it works: measure, decide, repeat", { placeholder: "title" });
  const stats = [["10", "games, 4 never tuned on"], ["120+", "measured runs"], ["≈ $45", "API cost"]];
  stats.forEach(([big, small], i) => {
    const y = 2.05 + i * 1.5;
    s.addText(big, { x: M, y, w: 3.4, h: 0.75, fontFace: HEAD, fontSize: 40, bold: true, color: ORANGE_DK, margin: 0, isTextBox: true });
    s.addText(small, { x: M, y: y + 0.75, w: 3.4, h: 0.55, fontFace: BODY, fontSize: 14, color: MUTED_L, margin: 0, valign: "top", isTextBox: true });
  });
  const ex = 4.55, ew = W - M - ex;
  s.addText("Built with Claude Code  ·  416 automated tests",
    { x: ex, y: 2.0, w: ew, h: 0.6, fontFace: BODY, fontSize: 16, color: TEXT_D, margin: 0, valign: "top", isTextBox: true });
  const epochs = [
    ["EPOCH 1", "Ablation", "kept only what wins"],
    ["EPOCH 2", "Tournament conditions", "selection = reality"],
    ["EPOCH 3", "Midpoint feedback · 2 experiments", "2 ideas rejected, weak runs traced and fixed"],
  ];
  epochs.forEach(([tag, title, desc], i) => {
    const y = 2.8 + i * 1.3;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: ex, y, w: ew, h: 1.12, rectRadius: 0.1, fill: { color: CARD }, line: { color: LINE_L, width: 0.75 }, shadow: shadow(), objectName: `epoch ${i + 1}` });
    s.addText(tag, { x: ex + 0.3, y: y + 0.2, w: 1.3, h: 0.35, fontFace: MONO, fontSize: 12, bold: true, color: ORANGE_DK, margin: 0, isTextBox: true });
    s.addText(title, { x: ex + 1.65, y: y + 0.16, w: ew - 1.95, h: 0.4, fontFace: HEAD, fontSize: 18, bold: true, color: TEXT_D, margin: 0, isTextBox: true });
    s.addText(desc, { x: ex + 1.65, y: y + 0.56, w: ew - 1.95, h: 0.5, fontFace: BODY, fontSize: 14, color: MUTED_L, margin: 0, valign: "top", isTextBox: true });
  });
  s.addNotes("(1:30–2:15) We built the harness the way it works itself. Claude Code was our engineering team: parallel agents wrote and reviewed every module, held to 416 tests. Then we ran loops. Besides the two dev games we built eight games of our own with new mechanics, and kept four of them out of all tuning, as an honest exam. Each epoch: switch features off one at a time, measure, keep only what wins. After the organisers' midpoint evaluation, Timo's two experiments rejected two ideas and traced our weak runs to the simulator check, which we fixed. More than 120 measured runs, about 45 dollars in total.");
}

pres.addSection({ title: "Close" });
// ---------------------------------------------------------------- 7 demo
{
  const s = pres.addSlide({ masterName: "DARK", sectionTitle: "Close" });
  eyebrow(s, "ONE REAL RUN, FROM SPEC TO STRATEGY.PY", ORANGE);
  s.addText("Spec in. Strategy out. Never a forfeit.", { placeholder: "title" });
  s.addText("Thank you for listening!", { x: M, y: 1.62, w: 9, h: 0.55, fontFace: HEAD, fontSize: 26, bold: true, color: ORANGE, margin: 0, isTextBox: true });
  const x0 = 0.9, x1 = 12.4, ay = 4.0, T = 180, sx = (x1 - x0) / T;
  const X = (t) => x0 + t * sx;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x0, y: ay - 0.05, w: x1 - x0, h: 0.1, rectRadius: 0.05, fill: { color: LINE_D }, line: { color: LINE_D, width: 0 }, objectName: "axis" });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x0, y: ay - 0.05, w: X(141) - x0, h: 0.1, rectRadius: 0.05, fill: { color: ORANGE }, line: { color: ORANGE, width: 0 }, objectName: "progress" });
  s.addText("0 s", { x: x0 - 0.3, y: ay + 0.2, w: 0.6, h: 0.3, fontFace: MONO, fontSize: 12, color: FAINT_D, align: "center", margin: 0, isTextBox: true });
  s.addText("deadline 180 s", { x: x1 - 1.8, y: ay + 0.2, w: 1.8, h: 0.3, fontFace: MONO, fontSize: 12, color: FAINT_D, align: "right", margin: 0, isTextBox: true });
  const ev = [
    [24, "first draft leads", true],
    [35, "simulator fails a check", false],
    [68, "simulator repaired", true],
    [98, "self-play ranking", false],
    [133, "a challenger joins", true],
    [141, "strategy delivered", false],
  ];
  ev.forEach(([t, label, above]) => {
    const cx = X(t);
    s.addShape(pres.shapes.OVAL, { x: cx - 0.11, y: ay - 0.11, w: 0.22, h: 0.22, fill: { color: t === 35 ? INK : ORANGE }, line: { color: ORANGE, width: 2 }, objectName: `event ${t}` });
    const ly = above ? ay - 1.25 : ay + 0.55;
    s.addText(`${t} s`, { x: cx - 0.11, y: ly, w: 1.2, h: 0.32, fontFace: MONO, fontSize: 14, bold: true, color: ORANGE, margin: 0, isTextBox: true });
    s.addText(label, { x: cx - 0.11, y: ly + 0.34, w: 2.35, h: 0.6, fontFace: BODY, fontSize: 14, color: TEXT_L, margin: 0, valign: "top", isTextBox: true });
  });
  s.addText([{ text: "141 seconds, 1.8 cents, one champion.  ", options: { bold: true, color: TEXT_L } },
              { text: "A real task-A run in the evaluation’s container: drafts written, the simulator checked against the spec and repaired, drafts ranked by self-play, a challenger tried, the champion delivered well before the deadline.", options: { color: MUTED_D } }],
    { x: M, y: 6.0, w: W - 2 * M, h: 0.75, fontFace: BODY, fontSize: 15, margin: 0, valign: "top", isTextBox: true });
  s.addNotes("(2:15–3:00) To close, this is what one real run looks like, recorded in the evaluation’s Docker container on the task-A budget. After 24 seconds the first valid draft leads. At 35 seconds the simulator fails a worked example from the spec, so it is not trusted; at 68 it is repaired and passes. Then the drafts play each other and get ranked, an improvement round adds a challenger, and at 141 of 180 seconds the champion is delivered, for 1.8 of 3 cents. Every step is checked by plain code, and the file on disk is a valid strategy at every second. Spec in, strategy out, never a forfeit. Thank you for listening!");
}

(async () => {
  await pres.writeFile({ fileName: OUT });
  try {
    const { applyTheme } = require(path.join(SKILL, "scripts", "apply_theme.js"));
    await applyTheme(OUT, THEME);
    console.log("theme applied");
  } catch (e) {
    console.log("theme not applied:", e.message);
  }
  console.log("wrote", OUT);
})();
