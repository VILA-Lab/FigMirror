"""figcopy_static — static webui assets + a small server-side helper.

Most of this directory is files served verbatim under /static-ui/<file>
(CSS, JS, mock_iters/). The single non-static module is :mod:`highlight`,
imported by :mod:`figcopy_serve` to syntax-highlight Python source for
the Export Code modal. ``_serve_static_ui`` filters by file extension
to keep .py files (this package init, highlight.py, mock_iters/*/code.py)
unreachable via HTTP.
"""
