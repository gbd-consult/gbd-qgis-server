"""Generate profiling data and QGIS projects. Runs inside the QGIS image.

gen_project.py <out-dir> [key=value ...]

options:
    seed           random seed
    polygons       polygons per side of the polygon grid (total = polygons^2)
    vertices       vertices per polygon
    points         number of label points
    layers         number of rendered polygon layers in slow.qgs
    hidden_layers  number of polygon layers in slow.qgs that are loaded, but not requested (project load cost)
    rules          number of rules per rule-based renderer
    hang_port      port of the blackhole server (started by profile.py in the server container), hung.qgs reads from it
"""

import json
import math
import os
import random
import sys

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsRuleBasedRenderer,
    QgsSymbol,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QMetaType
from qgis.PyQt.QtGui import QColor

DEFAULTS = {
    'seed': 1,
    'polygons': 150,
    'vertices': 64,
    'points': 20000,
    'layers': 30,
    'hidden_layers': 100,
    'rules': 40,
    'hang_port': 9999,
}

CRS = 'EPSG:3857'
CELL = 1000
CLASSES = 50


def main():
    out_dir = sys.argv[1]
    opts = dict(DEFAULTS)
    for a in sys.argv[2:]:
        key, _, val = a.partition('=')
        opts[key] = int(val)

    app = QgsApplication([], False)
    app.initQgis()

    rnd = random.Random(opts['seed'])
    gpkg = os.path.join(out_dir, 'data.gpkg')
    if os.path.exists(gpkg):
        os.unlink(gpkg)

    write_polygons(gpkg, rnd, opts['polygons'], opts['vertices'])
    write_points(gpkg, rnd, opts['polygons'], opts['points'])

    layers = write_slow_project(out_dir, gpkg, opts['layers'], opts['hidden_layers'], opts['rules'])
    write_hung_project(out_dir, opts['hang_port'])

    meta = {
        'opts': opts,
        'crs': CRS,
        'extent': [0, 0, opts['polygons'] * CELL, opts['polygons'] * CELL],
        'cell': CELL,
        'layers': layers,
    }
    with open(os.path.join(out_dir, 'meta.json'), 'w', encoding='utf8') as fp:
        json.dump(meta, fp, indent=4)

    for name in os.listdir(out_dir):
        os.chmod(os.path.join(out_dir, name), 0o644)

    app.exitQgis()


def new_writer(gpkg, layer_name, fields, geom_type):
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = 'GPKG'
    opts.layerName = layer_name
    if os.path.exists(gpkg):
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    return QgsVectorFileWriter.create(
        gpkg,
        fields,
        geom_type,
        QgsCoordinateReferenceSystem(CRS),
        QgsCoordinateTransformContext(),
        opts,
    )


def write_polygons(gpkg, rnd, side, vertices):
    fields = QgsFields()
    fields.append(QgsField('cls', QMetaType.Int))
    fields.append(QgsField('name', QMetaType.QString))

    w = new_writer(gpkg, 'polygons', fields, QgsWkbTypes.Polygon)
    for ix in range(side):
        for iy in range(side):
            cx = (ix + 0.5) * CELL
            cy = (iy + 0.5) * CELL
            ring = []
            for k in range(vertices):
                a = 2 * math.pi * k / vertices
                r = CELL * 0.45 * rnd.uniform(0.6, 1.0)
                ring.append(QgsPointXY(cx + r * math.cos(a), cy + r * math.sin(a)))
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromPolygonXY([ring]))
            f.setAttributes([rnd.randrange(CLASSES), f'polygon_{ix}_{iy}'])
            w.addFeature(f)
    del w


def write_points(gpkg, rnd, side, count):
    fields = QgsFields()
    fields.append(QgsField('name', QMetaType.QString))

    w = new_writer(gpkg, 'points', fields, QgsWkbTypes.Point)
    size = side * CELL
    for n in range(count):
        f = QgsFeature(fields)
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(rnd.uniform(0, size), rnd.uniform(0, size))))
        f.setAttributes([f'point_{n}'])
        w.addFeature(f)
    del w


def rule_renderer(geom_type, rules, layer_index):
    root = QgsRuleBasedRenderer.Rule(None)
    for n in range(rules):
        sym = QgsSymbol.defaultSymbol(geom_type)
        sym.setColor(QColor.fromHsv((n * 37 + layer_index * 11) % 360, 160, 220, 120))
        expr = f'"cls" % {rules} = {n} and length("name") > {n % 5}'
        root.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, expr, f'rule_{n}'))
    return QgsRuleBasedRenderer(root)


def write_slow_project(out_dir, gpkg, layers, hidden_layers, rules):
    prj = QgsProject.instance()
    prj.clear()
    prj.setCrs(QgsCoordinateReferenceSystem(CRS))

    for n in range(layers):
        lay = QgsVectorLayer(f'{gpkg}|layername=polygons', f'polygons_{n}', 'ogr')
        lay.setRenderer(rule_renderer(QgsWkbTypes.PolygonGeometry, rules, n))
        prj.addMapLayer(lay)

    for n in range(hidden_layers):
        lay = QgsVectorLayer(f'{gpkg}|layername=polygons', f'hidden_{n}', 'ogr')
        lay.setRenderer(rule_renderer(QgsWkbTypes.PolygonGeometry, rules, n))
        prj.addMapLayer(lay)

    lay = QgsVectorLayer(f'{gpkg}|layername=points', 'points', 'ogr')
    fmt = QgsTextFormat()
    fmt.setSize(9)
    pal = QgsPalLayerSettings()
    pal.fieldName = 'name'
    pal.setFormat(fmt)
    lay.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lay.setLabelsEnabled(True)
    prj.addMapLayer(lay)

    prj.write(os.path.join(out_dir, 'slow.qgs'))

    names = []
    for n in range(layers):
        names.append(f'polygons_{n}')
    names.append('points')
    return names


def write_hung_project(out_dir, port):
    prj = QgsProject.instance()
    prj.clear()
    prj.setCrs(QgsCoordinateReferenceSystem(CRS))

    lay = QgsVectorLayer(f'/vsicurl/http://127.0.0.1:{port}/hang.geojson', 'hung', 'ogr')
    prj.addMapLayer(lay)
    prj.write(os.path.join(out_dir, 'hung.qgs'))


if __name__ == '__main__':
    main()
