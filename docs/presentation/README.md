# Presentation

`slides.md` is a 9-slide, 5-7 minute deck in Markdown (title plus 8 slides, separated by `---`, presenter notes as `> Notes:` blockquotes with the time for each slide and the source of its numbers). It follows the story: problem, idea, pipeline, budget and deadline enforcement, evidence (calibration, experiment 1, midpoint evaluation and the bug it exposed, features kept or cut), overfitting, next steps.
Present with Marp: `npx @marp-team/marp-cli docs/presentation/slides.md` (add `--pdf` or `--pptx` to export, `-s` to serve), or open it in any Markdown viewer.
Marp renders the notes as part of the slide; delete the `> Notes:` blocks from an export if you do not want them shown. Numbers come from `docs/STATUS.md` and `report.md`; update them there first if a result changes.
