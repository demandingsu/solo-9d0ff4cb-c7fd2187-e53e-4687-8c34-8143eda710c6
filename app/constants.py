"""Shared constants for the A4 label sheet geometry."""

from __future__ import annotations

# A4 label paper is registered as a fixed 3-column by 8-row grid, i.e. 24
# equally sized cells numbered left-to-right, top-to-bottom starting at 1.
LABEL_SHEET_COLUMNS = 3
LABEL_SHEET_ROWS = 8
LABEL_SHEET_TOTAL_CELLS = LABEL_SHEET_COLUMNS * LABEL_SHEET_ROWS

# A placement in any of these states still holds its physical cell.
LABEL_ACTIVE_CELL_STATUSES = ("reserved", "printed", "damaged")

# A placement in any of these states still holds its box code.
LABEL_ACTIVE_BOX_STATUSES = ("reserved", "printed")
