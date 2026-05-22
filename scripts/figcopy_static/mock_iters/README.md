# mock_iters/

Pre-staged iter directories consumed by
`scripts/figcopy_runner/mock.py` (`MockRunner`).

Each `iter<N>/` contains generic-named files:

    img.png       — the iter's render
    code.py       — drawer's matplotlib script (mock)
    notes.md      — drawer's iter notes (mock)
    audit.json    — reviewer's verdict + anchors + focus themes

On `runner.start(workdir)`, `MockRunner` copies each iter's
files into the workdir at 4–8s intervals, renaming generic →
iter-numbered to match the skill's filename convention
(`img_iter<N>.png`, etc).

Regenerate via:

    python3 scripts/figcopy_static/mock_iters/_build.py
