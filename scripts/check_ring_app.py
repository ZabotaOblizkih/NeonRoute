import sys
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

from qfluentwidgets import ProgressRing, IndeterminateProgressRing, ProgressBar, PushButton, FluentIcon as FIF

print("=== 1. ProgressRing ===")
pr = ProgressRing()
pr_methods = [m for m in dir(pr) if not m.startswith("_") and callable(getattr(pr, m))]
print("Methods:", [m for m in pr_methods if any(k in m.lower() for k in ["val", "text", "set", "ring", "stroke"])])
print("Default size:", pr.sizeHint().width(), "x", pr.sizeHint().height())

print("\n=== 2. IndeterminateProgressRing ===")
ipr = IndeterminateProgressRing()
ipr_methods = [m for m in dir(ipr) if not m.startswith("_") and callable(getattr(ipr, m))]
print("Methods:", [m for m in ipr_methods if any(k in m.lower() for k in ["start", "stop", "set", "ring"])])

print("\n=== 3. ProgressBar ===")
pb = ProgressBar()
print("ProgressBar height:", pb.sizeHint().height())
