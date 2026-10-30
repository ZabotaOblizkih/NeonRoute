import sys
from qfluentwidgets import ProgressRing, IndeterminateProgressRing, PushButton, FluentIcon as FIF

print("=== ProgressRing ===")
pr = ProgressRing()
print("ProgressRing methods:", [m for m in dir(pr) if not m.startswith("_")][:20])
print("ProgressRing default size:", pr.sizeHint().width(), pr.sizeHint().height())

print("\n=== IndeterminateProgressRing ===")
ipr = IndeterminateProgressRing()
print("IndeterminateProgressRing methods:", [m for m in dir(ipr) if not m.startswith("_")][:20])
print("IndeterminateProgressRing default size:", ipr.sizeHint().width(), ipr.sizeHint().height())
