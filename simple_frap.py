#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ------------------------------------------------------------------------------
#   Copyright (C) 2017-2018 University of Dundee. All rights reserved.

#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.

#   You should have received a copy of the GNU General Public License along
#   with this program; if not, write to the Free Software Foundation, Inc.,
#   51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# ------------------------------------------------------------------------------

"""
Simple FRAP plots from Rectangles on images and creates an OMERO.figure.

This an OMERO script that runs server-side.
"""

import omero
import json
from cStringIO import StringIO

import omero.scripts as scripts
from omero.rtypes import rlong, rstring
from omero.gateway import BlitzGateway
from omeroweb.webgateway.marshal import imageMarshal

from PIL import Image
import numpy as np
try:
    import matplotlib.pyplot as plt
except (ImportError, RuntimeError):
    plt = None

JSON_FILEANN_NS = "omero.web.figure.json"

def create_figure_file(conn, figure_json):
    """Create Figure FileAnnotation from json data."""
    figure_name = figure_json['figureName']
    if len(figure_json['panels']) == 0:
        raise Exception('No Panels')
    first_img_id = figure_json['panels'][0]['imageId']

    # we store json in description field...
    description = {}
    description['name'] = figure_name
    description['imageId'] = first_img_id

    # Try to set Group context to the same as first image
    conn.SERVICE_OPTS.setOmeroGroup('-1')
    i = conn.getObject("Image", first_img_id)
    gid = i.getDetails().getGroup().getId()
    conn.SERVICE_OPTS.setOmeroGroup(gid)

    json_string = json.dumps(figure_json)
    file_size = len(json_string)
    f = StringIO()
    json.dump(figure_json, f)

    update = conn.getUpdateService()
    orig_file = conn.createOriginalFileFromFileObj(
        f, '', figure_name, file_size, mimetype="application/json")
    fa = omero.model.FileAnnotationI()
    fa.setFile(omero.model.OriginalFileI(orig_file.getId(), False))
    fa.setNs(rstring(JSON_FILEANN_NS))
    desc = json.dumps(description)
    fa.setDescription(rstring(desc))
    fa = update.saveAndReturnObject(fa, conn.SERVICE_OPTS)
    return fa.getId().getValue()


def get_panel_json(image, x, y, width, height, theT):
    """Get json for a figure panel."""
    px = image.getPrimaryPixels().getPhysicalSizeX()
    py = image.getPrimaryPixels().getPhysicalSizeY()

    rv = imageMarshal(image)

    img_json = {
        "labels":[],
        "height": height,
        "channels": rv['channels'],
        "width": width,
        "sizeT": rv['size']['t'],
        "sizeZ": rv['size']['z'],
        "dx": 0,
        "dy": 0,
        "rotation": 0,
        "imageId": image.id,
        "name": image.getName(),
        "orig_width": rv['size']['width'],
        "zoom": 100,
        "shapes": [],
        "orig_height": rv['size']['height'],
        "y": y,
        "x": x,
        "theT": theT,
        "theZ": rv['rdefs']['defaultZ']
    }
    if px is not None:
        img_json["pixel_size_x"] = px.getValue()
        img_json["pixel_size_x_unit"] = str(px.getUnit())
        img_json["pixel_size_x_symbol"] = px.getSymbol()
    if py is not None:
        img_json["pixel_size_y"] = py.getValue()
    return img_json

def create_omero_figure(conn, images, plots):
    """Create OMERO.figure from given FRAP images and plot images."""
    figure_json = {"version":2,
                   "paper_width":595,
                   "paper_height":842,
                   "page_size":"A4",
                   "figureName":"FRAP figure from script",
                  }
    time_frames = [0, 1, 2, 3, 5]

    panel_width = 80
    panel_height = panel_width
    spacing = panel_width/20
    margin = 40

    panels_json = []

    for i, image in enumerate(images):

        panel_x = margin
        panel_y = (i * (panel_height + spacing)) + margin
        for col in range(len(time_frames)):
            the_t = time_frames[col]
            panel_x = (col * (panel_height + spacing)) + margin
            j = get_panel_json(image, panel_x, panel_y, panel_width, panel_height, the_t)
            # j['labels'] = get_labels_json(j, c, z)
            panels_json.append(j)
        # Add plot
        if i < len(plots):
            plot = plots[i]
            panel_x = (len(time_frames) * (panel_height + spacing)) + margin
            plot_width = panel_height * (float(plot.getSizeX()) / plot.getSizeY())
            j = get_panel_json(plot, panel_x, panel_y, plot_width, panel_height, 0)
            panels_json.append(j)

    figure_json['panels'] = panels_json
    return create_figure_file(conn, figure_json)


def run(conn, params):
    """
    For each image, getTiles() for FRAP rectangle and plot mean intensity.

    Returns list of images
    @param conn   The BlitzGateway connection
    @param params The script parameters
    """
    images = []

    if params.get("Data_Type") == 'Dataset':
        for dsId in params["IDs"]:
            dataset = conn.getObject("Dataset", dsId)
            if dataset:
                for image in dataset.listChildren():
                    images.append(image)
    elif params.get("Data_Type") == 'Image':
        images = list(conn.getObjects('Image', params["IDs"]))

    if len(images) == 0:
        return None
    roi_service = conn.getRoiService()

    frap_plots = []

    for image in images:
        print "---- Processing image", image.id
        result = roi_service.findByImage(image.getId(), None)
        x = 0
        y = 0
        width = 0
        height = 0
        for roi in result.rois:
            print "ROI:  ID:", roi.getId().getValue()
            for s in roi.copyShapes():
                if type(s) == omero.model.RectangleI:
                    x = s.getX().getValue()
                    y = s.getY().getValue()
                    width = s.getWidth().getValue()
                    height = s.getHeight().getValue()
        print "Rectangle:", x, y, width, height
        if x == 0:
            print "  No Rectangle found for this image"
            continue

        c, z = 0, 0
        tile = (int(x), int(y), int(width), int(height))
        pixels = image.getPrimaryPixels()
        size_t = image.getSizeT()
        zct_list = [(z, c, t, tile) for t in range(size_t)]
        planes = pixels.getTiles(zct_list)
        meanvalues = []
        for i, p in enumerate(planes):
            meanvalues.append(p.mean())

        print meanvalues

        # Add values as a Map Annotation on the image
        key_value_data = [[str(t), str(meanvalues[t])] for t in range(size_t)]
        map_ann = omero.gateway.MapAnnotationWrapper(conn)
        namespace = "demo.simple_frap_data"
        map_ann.setNs(namespace)
        map_ann.setValue(key_value_data)
        map_ann.save()
        image.linkAnnotation(map_ann)

        if plt is not None:
            # Code from https://stackoverflow.com/questions/7821518/
            fig = plt.figure()
            plt.subplot(111)
            plt.plot(meanvalues)
            fig.canvas.draw()
            fig.savefig('plot.png')
            pil_img = Image.open('plot.png')
            np_array = np.asarray(pil_img)
            red = np_array[::, ::, 0]
            green = np_array[::, ::, 1]
            blue = np_array[::, ::, 2]
            plane_gen = iter([red, green, blue])
            plot_name = image.getName() + "_FRAP_plot"
            i = conn.createImageFromNumpySeq(plane_gen, plot_name, sizeC=3,
                                             dataset=image.getParent())
            frap_plots.append(i)

    return create_omero_figure(conn, images, frap_plots)


if __name__ == "__main__":
    dataTypes = [rstring('Dataset'), rstring('Image')]
    client = scripts.client(
        'Simple_FRAP.py',
        """
    This script does simple FRAP analysis using Rectangle ROIs previously
    saved on images. If matplotlib is installed, data is plotted and new
    OMERO images are created from the plots.
        """,
        scripts.String(
            "Data_Type", optional=False, grouping="1",
            description="Choose source of images",
            values=dataTypes, default="Dataset"),

        scripts.List(
            "IDs", optional=False, grouping="2",
            description="Dataset or Image IDs.").ofType(rlong(0)),

        authors=["Will Moore", "OME Team"],
        institutions=["University of Dundee"],
        contact="ome-users@lists.openmicroscopy.org.uk",
    )

    try:
        # process the list of args above.
        scriptParams = {}
        for key in client.getInputKeys():
            if client.getInput(key):
                scriptParams[key] = client.getInput(key, unwrap=True)
        print scriptParams

        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)
        # Call the main script - returns the new OMERO.figure ann ID
        figure_id = run(conn, scriptParams)
        if figure_id is None:
            message = "No images found"
        else:
            message = "Created FRAP figure: %s" % figure_id

        client.setOutput("Message", rstring(message))

    finally:
        client.closeSession()
