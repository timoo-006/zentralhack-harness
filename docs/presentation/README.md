# Presentation

| file | what |
|------|------|
| **`pitch.pptx`** / **`pitch.pdf`** | **the 3-minute pitch** (7 slides, speaker notes with timings in the PPTX) |
| `slides.md` | a longer 9-slide draft in Markdown (Marp), kept as a source of extra material; updated at 14:15 with the midpoint evaluation, the simulator-check fix and experiments 3-4 |
| `build/` | the generator of the pitch: `cd build && npm install && node build.js ../pitch.pptx` |

Cover: Elias · Isaak · Timo. Slides carry only the core message; the details are in the
speaker notes (with timings). The PDF is exported from
PowerPoint (fonts embedded); the PPTX uses Century Gothic, Calibri and Consolas, which ship
with Office on Windows and macOS.

**Demo (3 minutes total, so no live wait):** slide 7 shows a real recorded run. Optional live
version: start `./run.sh tasks/beta-A` on a second screen when the pitch begins (~2.5 min,
~$0.02) and show `exit code 0` plus `arena check ... PASSED` on the last slide.

`slides.md`: title plus 8 slides separated by `---`, presenter notes as `> Notes:`
blockquotes with the time and the source of the numbers. Render it with Marp:
`npx @marp-team/marp-cli docs/presentation/slides.md` (add `--pdf` or `--pptx` to export).
Numbers come from `docs/STATUS.md` and `report.md`; update them there first if a result
changes.
