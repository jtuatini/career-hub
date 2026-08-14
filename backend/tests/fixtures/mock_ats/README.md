Serve with `python3 -m http.server 8455 -d backend/tests/fixtures/mock_ats` for extension e2e; used by the one-click-apply verification recipe, not by pytest.

`real_widgets.html` models the widget shapes real ATSes use where `index.html` is
too idealized: react-select-style comboboxes (both input-role and wrapper-role
variants) whose committed value ignores raw `.value` writes and commits only on
option mousedown, Lever-style question text outside any `<label>`, and Yes/No
radio groups. `e2e_real_widgets.cjs` drives the REAL `extension/content.js`
against it headlessly (snapshot classification + executor, incl. the
never-submit guard):

    NODE_PATH=<dir with playwright> node e2e_real_widgets.cjs

It starts with a negative control proving raw native-setter writes do NOT stick
on the combobox — if that control ever fails, the fixture has stopped modeling
the bug that motivated it.
