"""八訂の本表そのままの見出しで列を見つけられること（ADR-019 D1）。

実物は「食　品　名」「廃　棄　率」のように全角空白で字を離し、「エネルギー」は kJ と kcal の
結合セルの左側にしか載らない。2026-09-13 に実物の取り込みで見出しが見つからず、直した。
"""

from manor.staff.chef.nutrition import find_columns


def _rows() -> list[list[object]]:
    blank = [""] * 12
    return [
        blank,
        ["食　品　群", "食　品　番　号", "索　引　番　号", "可　食　部　100", "", "", "", "", "", "", "", ""],
        ["", "", "", "食　品　名", "廃　棄　率", "エネルギー", "", "水　分", "たんぱく質", "", "脂質", "食塩相当量"],
        ["", "", "", "", "", "", "", "", "アミノ酸組成による\nたんぱく質", "たんぱく質", "脂質", ""],
        ["", "", "", "単位", "%", "kJ", "kcal", "g", "", "", "", "g"],
        ["", "", "", "成分識別子", "REFUSE", "ENERC", "ENERC_KCAL", "WATER", "PROTCAA", "PROT-", "FAT-", "NACL_EQ"],
        ["01", "01001", "0001", "アマランサス　玄穀", "0", "1452", "343", "13.5", "(11.3)", "12.7", "6.0", "0"],
    ]


def test_全角空白の見出しと結合セルの見出しで列が見つかる() -> None:
    start, columns = find_columns(_rows())
    assert start == 6
    assert columns["food_code"] == 1
    assert columns["name"] == 3
    assert columns["refuse_pct"] == 4
    assert columns["kcal"] == 6, "kJ の列（5）ではなく kcal の列を採る"
    assert columns["protein_g"] == 9, "アミノ酸組成による方（8）ではなく素のたんぱく質"
    assert columns["fat_g"] == 10
    assert columns["salt_g"] == 11
