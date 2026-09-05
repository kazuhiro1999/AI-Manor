"""部下（staff）のプラグイン置き場（ADR-001 §11）。

`src/manor/staff/<name>/` に `__init__.py`・`schema.sql`・`cli.py` を置くと、
`manor.db.init()` がスキーマを、`manor.cli` の起動時登録が CLI を自動で拾う。
列挙は `pkgutil.iter_modules(staff.__path__)` で行う。

このパッケージの `__path__` は（他の通常パッケージと同じく）素の `list`。
試験はここに一時ディレクトリを追加するだけで、偽の部下パッケージを登録できる:

    import manor.staff as staff_pkg
    staff_pkg.__path__.append(str(tmp_dir_containing_fake_package))
"""

from __future__ import annotations
