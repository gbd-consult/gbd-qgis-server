"""Print pixel colors of an image at relative positions. Runs inside the image.

pixels.py <image-path> <x,y> [<x,y> ...]
    x and y are relative (0..1), prints "r g b" for each position
"""

import sys

try:
    from PyQt6.QtGui import QGuiApplication, QImage
except ImportError:
    from PyQt5.QtGui import QGuiApplication, QImage


def main():
    app = QGuiApplication([])

    img = QImage(sys.argv[1])
    if img.isNull():
        sys.exit(f'cannot read {sys.argv[1]}')

    for pos in sys.argv[2:]:
        rx, ry = pos.split(',')
        c = img.pixelColor(int(float(rx) * (img.width() - 1)), int(float(ry) * (img.height() - 1)))
        print(c.red(), c.green(), c.blue())


if __name__ == '__main__':
    main()
