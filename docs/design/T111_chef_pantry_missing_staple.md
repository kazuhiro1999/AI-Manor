# T111 — chefのpantry missingが基礎調味料の「切れ」を無視する

## 経緯

v1 `agents/kitchen/BACKLOG.md` のK9（2026-08-27発見、裁定未了のまま残存）:

> `pantry.ps1 -Missing` が「切れ」を無視する。基礎調味料は `Test-Basic` で真っ先に
> 「常備」判定され、`PANTRY.md` に `切れ` と記録されていても `-Missing` は常に
> 「常備として扱った」を返す。kitchen.md は「切れたら以後は足りない扱いにする」と
> 明言しており、道具の挙動と定義がずれている。
> → K3の範囲外。直すなら `-Missing` の基礎調味料判定を「`Test-Basic` かつ
> `PANTRY.md` に `切れ` 行が無い」に絞る必要があり、既存ケース（醤油＝常に常備）を
> 仕込みごと見直す規模になる。次の周で1テーマとして扱う（＝未着手のまま凍結）。

## v2での現状（2026-10-02夜勤N3で確認）

- `src/manor/staff/chef/ops.py` の `check_missing()`（181〜195行）は、要求品が
  `is_staple(req, staples)` で真なら、`pantry_items`（実在庫）の中身を一切見ずに
  結果から除外する。
- `_pantry_items()`（`cli.py:36`）は `chef_pantry` テーブルの行をそのまま返す設計
  なので、在庫が切れていれば行自体が無いはず——にもかかわらず、stapleは行の有無を
  見ずに常に「常備」扱いになる。
- `tests/staff/test_chef.py::test_check_missing_excludes_staples_and_reports_found`
  と `tests/staff/test_chef_menu.py::test_staples_do_not_count_as_pantry_hits` が、
  この「stapleは無条件除外」を**仕様として固定**している。

→ **K9と同一の欠陥が、裁定されないままv2へそのまま移植された。**

## 判断が要ること

- 直すか現状維持か（基礎調味料が切れる頻度・実害次第）。K9自体、v1では「次の周で
  1テーマ」として凍結されたまま主人の裁定が無い。
- 直すなら: `is_staple` 判定を「stapleかつ`pantry_items`に実在する」場合のみ除外
  する形に変え、上記2テストのケース設計も見直す。
