"""Overfull-vbox detection from pdflatex logs."""

from pathlib import Path

from app.services.latex import CompileInfo, _parse_overfull, compile_tex_info

CLEAN_LOG = "This is pdfTeX\nOutput written on doc.pdf (1 page, 20000 bytes).\n"
ONE_OVERFULL = CLEAN_LOG + "Overfull \\vbox (14.05398pt too high) has occurred while \\output is active\n"
MANY_OVERFULL = (
    CLEAN_LOG
    + "Overfull \\vbox (3.2pt too high) has occurred while \\output is active\n"
    + "Overfull \\vbox (27.90pt too high) has occurred while \\output is active\n"
)


def test_parse_overfull_none():
    assert _parse_overfull(CLEAN_LOG) == 0.0


def test_parse_overfull_single_and_max():
    assert _parse_overfull(ONE_OVERFULL) == 14.05398
    assert _parse_overfull(MANY_OVERFULL) == 27.90


def test_compile_tex_info_real_compile(tmp_path):
    tex = "\\documentclass{article}\\begin{document}hello overflow world\\end{document}"
    pdf, info = compile_tex_info(tex, tmp_path, "probe")
    assert pdf.exists() and pdf.suffix == ".pdf"
    assert isinstance(info, CompileInfo)
    assert info.page_count == 1
    assert info.overfull_vbox_pt == 0.0
