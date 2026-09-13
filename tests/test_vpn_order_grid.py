import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from ui.views.accounts import VpnOrderListWidget


def _app():
    return QApplication.instance() or QApplication([])


def test_vpn_grid_swaps_values_and_renumbers_positions():
    app = _app()
    widget = VpnOrderListWidget()
    for code in ("DE", "US", "GB", "AR", "DIRECT"):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, code)
        widget.addItem(item)
    widget.refresh_numbering()

    emitted = []
    widget.items_swapped.connect(lambda: emitted.append(True))
    assert widget.swap_rows(0, 4) is True

    assert [
        widget.item(row).data(Qt.ItemDataRole.UserRole)
        for row in range(widget.count())
    ] == ["DIRECT", "US", "GB", "AR", "DE"]
    assert widget.item(0).text().startswith("1.")
    assert widget.item(4).text().startswith("5.")
    assert emitted == [True]
    widget.close()
    app.processEvents()


def test_vpn_grid_uses_three_equal_columns():
    app = _app()
    widget = VpnOrderListWidget()
    widget.resize(900, 98)
    widget.show()
    app.processEvents()

    column_width = widget.gridSize().width()
    assert 285 <= column_width <= 300
    assert widget.gridSize().height() == 32
    assert widget.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert widget.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    for code in ("DE", "US", "GB", "AR", "DIRECT"):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, code)
        widget.addItem(item)
    app.processEvents()
    assert len({widget.visualItemRect(widget.item(row)).y() for row in range(3)}) == 1
    assert widget.visualItemRect(widget.item(3)).y() > widget.visualItemRect(widget.item(0)).y()
    widget.close()
