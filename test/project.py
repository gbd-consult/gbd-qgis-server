"""Create the test data and project in /T. Runs inside the image."""

import json
import os
import xml.etree.ElementTree as ET

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsLayoutFrame,
    QgsLayoutItemHtml,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsMarkerSymbol,
    QgsMultiBandColorRenderer,
    QgsPalLayerSettings,
    QgsPrintLayout,
    QgsProject,
    QgsRasterBlock,
    QgsRasterFileWriter,
    QgsRasterLayer,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsSvgMarkerSymbolLayer,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)

DIR = '/T'

# files for the proxy tests, served by proxy.py as http://upstream/<name>
UPSTREAM_DIR = f'{DIR}/upstream'
RASTER_PATH = f'{UPSTREAM_DIR}/raster.tif'
RASTER_URL = '/vsicurl/http://upstream/raster.tif'
SVG_URL = 'http://upstream/blue.svg'
MAGENTA = (255, 0, 255)

POLY = {
    'type': 'FeatureCollection',
    'features': [
        {
            'type': 'Feature',
            'properties': {'name': 'Area'},
            'geometry': {'type': 'Polygon', 'coordinates': [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]},
        },
    ],
}

POINTS = {
    'type': 'FeatureCollection',
    'features': [],
}
for i in range(4):
    POINTS['features'].append(
        {
            'type': 'Feature',
            'properties': {'name': f'P{i}'},
            'geometry': {'type': 'Point', 'coordinates': [1 + i * 2, 5]},
        }
    )

BLUE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
<rect x="0" y="0" width="100" height="100" fill="#0000ff"/>
</svg>
"""

HTML_FRAME = '<html><body style="margin:0;background-color:#ff0000;height:2000px"></body></html>'
HTML_LABEL = '<html><body style="margin:0;background-color:#ffff00;height:2000px"></body></html>'


def write(name, text):
    with open(f'{DIR}/{name}', 'wt', encoding='utf8') as fp:
        fp.write(text)


def main():
    app = QgsApplication([], False)
    app.setPrefixPath('/usr', True)
    app.initQgis()

    write('poly.geojson', json.dumps(POLY))
    write('points.geojson', json.dumps(POINTS))
    write('blue.svg', BLUE_SVG)

    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem('EPSG:4326'))

    poly = QgsVectorLayer(f'{DIR}/poly.geojson', 'poly', 'ogr')
    poly.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({'color': '0,170,0', 'outline_color': '0,0,0'})))

    points = QgsVectorLayer(f'{DIR}/points.geojson', 'points', 'ogr')
    sym = QgsMarkerSymbol()
    sym.changeSymbolLayer(0, QgsSvgMarkerSymbolLayer('alkis3406_1.svg', 8))
    points.setRenderer(QgsSingleSymbolRenderer(sym))
    pls = QgsPalLayerSettings()
    pls.fieldName = 'name'
    pls.enabled = True
    points.setLabeling(QgsVectorLayerSimpleLabeling(pls))
    points.setLabelsEnabled(True)

    if not poly.isValid() or not points.isValid():
        raise ValueError('invalid layers')

    project.addMapLayers([poly, points])
    project.writeEntry('WMSServiceCapabilities', '/', True)
    project.writeEntry('WMSExtent', '/', ['-1', '-1', '11', '11'])
    project.writeEntry('WFSLayers', '/', [poly.id(), points.id()])

    # A4 landscape, 297 x 210 mm

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName('test')

    map_item = QgsLayoutItemMap(layout)
    map_item.attemptMove(QgsLayoutPoint(10, 10))
    map_item.attemptResize(QgsLayoutSize(180, 180))
    map_item.setExtent(QgsRectangle(-1, -1, 11, 11))
    layout.addLayoutItem(map_item)

    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(f'{DIR}/blue.svg')
    picture.setResizeMode(QgsLayoutItemPicture.Stretch)
    picture.attemptMove(QgsLayoutPoint(200, 10))
    picture.attemptResize(QgsLayoutSize(80, 40))
    layout.addLayoutItem(picture)

    html = QgsLayoutItemHtml(layout)
    frame = QgsLayoutFrame(layout, html)
    frame.attemptMove(QgsLayoutPoint(200, 60))
    frame.attemptResize(QgsLayoutSize(80, 40))
    html.addFrame(frame)
    html.setContentMode(QgsLayoutItemHtml.ManualHtml)
    html.setHtml(HTML_FRAME)
    html.loadHtml()
    layout.addMultiFrame(html)

    label = QgsLayoutItemLabel(layout)
    label.setMode(QgsLayoutItemLabel.ModeHtml)
    label.setText(HTML_LABEL)
    label.attemptMove(QgsLayoutPoint(200, 110))
    label.attemptResize(QgsLayoutSize(80, 40))
    layout.addLayoutItem(label)

    project.layoutManager().addLayout(layout)

    if not project.write(f'{DIR}/test.qgs'):
        raise ValueError('cannot write project')

    create_proxy_project()

    for dp, dn, fn in os.walk(DIR):
        for name in dn + fn:
            os.chmod(os.path.join(dp, name), 0o777)


def create_proxy_project():
    # the raster is read by gdal (/vsicurl/), the svg picture by the qgis network manager,
    # these use different proxy settings

    os.makedirs(UPSTREAM_DIR, exist_ok=True)
    write('upstream/blue.svg', BLUE_SVG)
    create_raster(RASTER_PATH, MAGENTA)

    project = QgsProject()
    project.setCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
    project.setFilePathStorage(Qgis.FilePathType.Absolute)

    raster = QgsRasterLayer(RASTER_PATH, 'raster', 'gdal')
    if not raster.isValid():
        raise ValueError('invalid raster layer')
    raster.setRenderer(QgsMultiBandColorRenderer(raster.dataProvider(), 1, 2, 3))

    project.addMapLayer(raster)
    project.writeEntry('WMSServiceCapabilities', '/', True)
    project.writeEntry('WMSExtent', '/', ['-1', '-1', '11', '11'])

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName('proxy')

    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(SVG_URL, Qgis.PictureFormat.SVG)
    picture.setResizeMode(QgsLayoutItemPicture.Stretch)
    picture.attemptMove(QgsLayoutPoint(200, 10))
    picture.attemptResize(QgsLayoutSize(80, 40))
    layout.addLayoutItem(picture)

    project.layoutManager().addLayout(layout)

    path = f'{DIR}/proxy.qgs'
    if not project.write(path):
        raise ValueError('cannot write proxy project')

    # the remote raster cannot be opened here, so the project is written with the local file and the source is replaced
    replace_xml_value(path, RASTER_PATH, RASTER_URL)


def create_raster(path, color):
    writer = QgsRasterFileWriter(path)
    provider = writer.createMultiBandRaster(Qgis.DataType.Byte, 10, 10, QgsRectangle(0, 0, 10, 10), QgsCoordinateReferenceSystem('EPSG:4326'), 3)
    if not provider:
        raise ValueError(f'cannot create {path}')

    provider.setEditable(True)
    for band, val in enumerate(color, 1):
        block = QgsRasterBlock(Qgis.DataType.Byte, 10, 10)
        block.fill(val)
        provider.writeBlock(block, band, 0, 0)
    provider.setEditable(False)
    del provider


def replace_xml_value(path, old, new):
    tree = ET.parse(path)
    count = 0
    for el in tree.iter():
        if el.text == old:
            el.text = new
            count += 1
        for key, val in list(el.attrib.items()):
            if val == old:
                el.set(key, new)
                count += 1
    if not count:
        raise ValueError(f'{old!r} not found in {path}')
    tree.write(path, encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    main()
