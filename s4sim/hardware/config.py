# Copyright (c) 2020-2020 CMB-S4 Collaboration.
# Full license can be found in the top level "LICENSE" file.
"""Hardware configuration utilities.
"""

import os
import re
import copy

from collections import OrderedDict

import gzip

import numpy as np

import toml


class Hardware(object):
    """Class representing a specific hardware configuration.

    The data is stored in a dictionary, and can be loaded / dumped to disk
    as well as trimmed to include only a subset of detectors.

    Args:
        path (str, optional): If specified, configuration is loaded from this
            file during construction.

    """

    def __init__(self, path=None):
        self.data = OrderedDict()
        if path is not None:
            self.load(path)

    def dump(self, path, overwrite=False, compress=False):
        """Write hardware config to a TOML file.

        Dump data to a TOML format file, optionally compressing the contents
        with gzip and optionally overwriting the file.

        Args:
            path (str): The file to write.
            overwrite (bool): If True, overwrite the file if it exists.
                If False, then existing files will cause an exception.
            compress (bool): If True, compress the data with gzip on write.

        Returns:
            None

        """
        if os.path.exists(path):
            if overwrite:
                os.remove(path)
            else:
                raise RuntimeError(
                    "Dump path {} already exists.  Use overwrite option".format(path)
                )
        if compress:
            with gzip.open(path, "wb") as f:
                dstr = toml.dumps(self.data)
                f.write(dstr.encode())
        else:
            with open(path, "w") as f:
                dstr = toml.dumps(self.data)
                f.write(dstr)
        return

    def load(self, path):
        """Read data from a TOML file.

        The file can either be regular text or a gzipped version of a TOML
        file.

        Args:
            path (str): The file to read.

        Returns:
            None

        """
        dstr = None
        try:
            with gzip.open(path, "rb") as f:
                dstr = f.read()
                self.data = toml.loads(dstr.decode())
        except OSError:
            with open(path, "r") as f:
                dstr = f.read()
                self.data = toml.loads(dstr)
        return

    def wafer_map(self):
        """Construct wafer mapping to other auxilliary data.

        Given the current data state, build dictionaries to go from wafers
        to all other non-detector info:  telescopes, tubes, cards, crates,
        and bands.  This is a convenient mapping when pruning the hardware
        information or doing other kinds of lookups.

        Returns:
            (dict): Nested dictionaries from wafers to other properties.

        """
        result = OrderedDict()

        tube_to_tele = dict()
        for tele, props in self.data["telescopes"].items():
            for tb in props["tubes"]:
                tube_to_tele[tb] = tele

        wafer_to_tube = dict()
        for tb, props in self.data["tubes"].items():
            for wf in props["wafers"]:
                wafer_to_tube[wf] = tb

        crate_to_card = dict()
        for crate, props in self.data["crates"].items():
            for card in props["cards"]:
                crate_to_card[card] = crate

        result["cards"] = {x: y["card"] for x, y in self.data["wafers"].items()}
        result["crates"] = {
            x: crate_to_card[y["card"]] for x, y in self.data["wafers"].items()
        }
        result["bands"] = {x: y["bands"] for x, y in self.data["wafers"].items()}
        result["tubes"] = wafer_to_tube
        result["telescopes"] = {
            x: tube_to_tele[wafer_to_tube[x]] for x in list(self.data["wafers"].keys())
        }
        return result

    def select(self, telescopes=None, tubes=None, match=dict()):
        """Select a subset of detectors.

        Select detectors whose properties match some criteria.  A new Hardware
        object is created and returned.  If a matching expression is not
        specified for a given property name, then this is equivalent to
        selecting all values of that property.

        Before selecting on detector properties, any telescope / tube filtering
        criteria are first applied.

        Each key of the "match" dictionary should be the name of a detector
        property to be considered for selection (e.g. band, wafer, pol, pixel).
        The value is a matching expression which can be:

            - A list of explicit values to match.
            - A string containing a regex expression to apply.

        Example:
            Imagine you wanted to select all 90GHz detectors on wafers 25 and
            26 which have "A" polarization and are located in pixels 20-29
            (recall the "." matches a single character)::

                new = hw.select(match={"wafer": ["25", "26"],
                                "band": "MF.1",
                                "pol": "A",
                                "pixel": "02."})

        Args:
            telescopes (str): A regex string to apply to telescope names or a
                list of explicit names.
            tubes (str): A regex string to apply to tube names or a list of
                explicit names.
            match (dict): The dictionary of property names and their matching
                expressions.

        Returns:
            (Hardware): A new Hardware instance with the selected detectors.

        """
        # First parse any telescope and tube options into a list of wafers
        wselect = None
        tbselect = None
        if telescopes is not None:
            tbselect = list()
            for tele in telescopes:
                tbselect.extend(self.data["telescopes"][tele]["tubes"])
        if tubes is not None:
            if tbselect is None:
                tbselect = list()
            tbselect.extend(tubes)
        if tbselect is not None:
            wselect = list()
            for tb in tbselect:
                wselect.extend(self.data["tubes"][tb]["wafers"])

        dets = self.data["detectors"]

        # Build regex matches for each property
        reg = dict()
        if "wafer" in match:
            # Handle wafer case separately, since we need to merge any
            # match with our telescope / tube selection of wafers above.
            k = "wafer"
            v = match[k]
            if wselect is None:
                # Just the regular behavior
                if isinstance(v, list):
                    reg[k] = re.compile(r"(^" + "$|^".join(v) + r"$)")
                else:
                    reg[k] = re.compile(v)
            else:
                # Merge our selection
                wall = list(wselect)
                if isinstance(v, list):
                    wall.extend(v)
                else:
                    wall.append(v)
                reg[k] = re.compile(r"(^" + "$|^".join(wall) + r"$)")
        elif wselect is not None:
            # No pattern in the match dictionary, just our list from the
            # telescope / tube selection.
            reg["wafer"] = re.compile(r"(^" + "$|^".join(wselect) + r"$)")

        for k, v in match.items():
            if k == "wafer":
                # Already handled above
                continue
            else:
                if isinstance(v, list):
                    reg[k] = re.compile(r"(^" + "$|^".join(v) + r"$)")
                else:
                    reg[k] = re.compile(v)

        # Go through all detectors selecting things that match all fields
        newwafers = set()
        newdets = OrderedDict()
        for d, props in dets.items():
            keep = True
            for k, v in reg.items():
                if k in props:
                    test = v.match(props[k])
                    if test is None:
                        keep = False
                        break
            if keep:
                newwafers.add(props["wafer"])
                newdets[d] = copy.deepcopy(props)

        # Now compute the reduced set of auxilliary data needed for these
        # detectors.
        wafermap = self.wafer_map()

        # Copy this data
        hw = Hardware()
        hw.data = OrderedDict()
        for k, v in wafermap.items():
            hw.data[k] = OrderedDict()
            tocopy = set()
            for wf in newwafers:
                if isinstance(v[wf], list):
                    for iv in v[wf]:
                        tocopy.add(iv)
                else:
                    tocopy.add(v[wf])
            for elem in tocopy:
                hw.data[k][elem] = copy.deepcopy(self.data[k][elem])

        # Copy over the wafer data
        hw.data["wafers"] = OrderedDict()
        for wf in newwafers:
            hw.data["wafers"][wf] = copy.deepcopy(self.data["wafers"][wf])

        # And the detectors...
        hw.data["detectors"] = newdets

        return hw


def get_example():
    """Return an example Hardware config with the required sections.

    The returned Hardware object has 4 fake detectors as an example.  These
    detectors can be replaced by the results of other simulation functions.

    Returns:
        (Hardware): Hardware object with example parameters.

    """
    cnf = OrderedDict()

    bands = OrderedDict()

    bnd = OrderedDict()
    bnd["center"] = 20.0
    bnd["low"] = 17.5
    bnd["high"] = 22.5
    bnd["bandpass"] = ""
    bnd["NET"] = 473.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    # Noise elevation scaling fits from Carlos Sierra
    # These numbers are for V3 LAT baseline
    bnd["A"] = 0.09
    bnd["C"] = 0.87
    bands["ULFPL1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 27.0
    bnd["low"] = 24.0
    bnd["high"] = 30.0
    bnd["bandpass"] = ""
    bnd["NET"] = 429.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    # Noise elevation scaling fits from Carlos Sierra
    # These numbers are for V3 LAT baseline
    bnd["A"] = 0.09
    bnd["C"] = 0.87
    bands["LFL1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 39.0
    bnd["low"] = 30.0
    bnd["high"] = 48.0
    bnd["bandpass"] = ""
    bnd["NET"] = 261.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.25
    bnd["C"] = 0.64
    bands["LFL2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 27.0
    bnd["low"] = 24.0
    bnd["high"] = 30.0
    bnd["bandpass"] = ""
    bnd["NET"] = 445.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    # Noise elevation scaling fits from Carlos Sierra
    # These numbers are for V3 LAT baseline
    bnd["A"] = 0.09
    bnd["C"] = 0.87
    bands["LFPL1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 39.0
    bnd["low"] = 30.0
    bnd["high"] = 48.0
    bnd["bandpass"] = ""
    bnd["NET"] = 320.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.25
    bnd["C"] = 0.64
    bands["LFPL2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 30.0
    bnd["low"] = 25.5
    bnd["high"] = 34.5
    bnd["bandpass"] = ""
    bnd["NET"] = 177.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    # Noise elevation scaling fits from Carlos Sierra
    # These numbers are for V3 LAT baseline
    bnd["A"] = 0.09
    bnd["C"] = 0.87
    bands["LFS1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 40.0
    bnd["low"] = 34.0
    bnd["high"] = 46.0
    bnd["bandpass"] = ""
    bnd["NET"] = 224.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.25
    bnd["C"] = 0.64
    bands["LFS2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 93.0
    bnd["low"] = 75.5
    bnd["high"] = 110.5
    bnd["bandpass"] = ""
    bnd["NET"] = 305.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.14
    bnd["C"] = 0.80
    bands["MFL1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 145.0
    bnd["low"] = 125.0
    bnd["high"] = 165.0
    bnd["bandpass"] = ""
    bnd["NET"] = 385.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.17
    bnd["C"] = 0.76
    bands["MFL2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 93.0
    bnd["low"] = 75.5
    bnd["high"] = 110.5
    bnd["bandpass"] = ""
    bnd["NET"] = 347.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.14
    bnd["C"] = 0.80
    bands["MFPL1"] = bnd
    
    bnd = OrderedDict()
    bnd["center"] = 145.0
    bnd["low"] = 125.0
    bnd["high"] = 165.0
    bnd["bandpass"] = ""
    bnd["NET"] = 374.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.17
    bnd["C"] = 0.76
    bands["MFPL2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 85.0
    bnd["low"] = 74.8
    bnd["high"] = 95.2
    bnd["bandpass"] = ""
    bnd["NET"] = 270.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.14
    bnd["C"] = 0.80
    bands["MFLS1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 145.1
    bnd["low"] = 129.1
    bnd["high"] = 161.0
    bnd["bandpass"] = ""
    bnd["NET"] = 309.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.17
    bnd["C"] = 0.76
    bands["MFLS2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 95.0
    bnd["low"] = 83.6
    bnd["high"] = 106.4
    bnd["bandpass"] = ""
    bnd["NET"] = 238.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.14
    bnd["C"] = 0.80
    bands["MFHS1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 155.1
    bnd["low"] = 138.0
    bnd["high"] = 172.1
    bnd["bandpass"] = ""
    bnd["NET"] = 331.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.17
    bnd["C"] = 0.76
    bands["MFHS2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 225.0
    bnd["low"] = 195.0
    bnd["high"] = 255.0
    bnd["bandpass"] = ""
    bnd["NET"] = 792.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.30
    bnd["C"] = 0.58
    bands["HFL1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 278.0
    bnd["low"] = 255.5
    bnd["high"] = 300.5
    bnd["bandpass"] = ""
    bnd["NET"] = 1990.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.36
    bnd["C"] = 0.49
    bands["HFL2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 225.0
    bnd["low"] = 195.0
    bnd["high"] = 255.0
    bnd["bandpass"] = ""
    bnd["NET"] = 664.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.30
    bnd["C"] = 0.58
    bands["HFPL1"] = bnd
    
    bnd = OrderedDict()
    bnd["center"] = 278.0
    bnd["low"] = 255.5
    bnd["high"] = 300.5
    bnd["bandpass"] = ""
    bnd["NET"] = 1609.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.36
    bnd["C"] = 0.49
    bands["HFPL2"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 220.0
    bnd["low"] = 195.8
    bnd["high"] = 244.2
    bnd["bandpass"] = ""
    bnd["NET"] = 747.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.30
    bnd["C"] = 0.58
    bands["HFS1"] = bnd

    bnd = OrderedDict()
    bnd["center"] = 270.0
    bnd["low"] = 240.3
    bnd["high"] = 299.7
    bnd["bandpass"] = ""
    bnd["NET"] = 1281.0
    bnd["fknee"] = 50.0
    bnd["fmin"] = 0.01
    bnd["alpha"] = 3.5
    bnd["A"] = 0.36
    bnd["C"] = 0.49
    bands["HFS2"] = bnd

    cnf["bands"] = bands

    wafers = OrderedDict()

    wtypes = ["ULFPL", "LFL", "LFPL", "LFS", "MFL", "MFPL", "MFLS", "MFHS", "HFL", "HFPL", "HFS"]
    # partial wafers will be counted as individual wafers since we make a full wafer then kill pixels
    wcnt = {
        "ULFPL": 4,
        "LFL": 8*2,
        "LFPL": 9,
        "LFS": (12) * 2,
        "MFL": 54*2,
        "MFPL": 54,
        "MFLS": (12) * 6,
        "MFHS": (12) * 6,
        "HFL": 23*2,
        "HFPL": 18,
        "HFS": (6 + 6) * 4,
    }
    wpac = {
        "ULFPL": "RP",
        "LFL": "RP",
        "LFPL": "RP",
        "LFS": "RP",
        "MFL": "RP",
        "MFPL": "RP",
        "MFLS": "RP",
        "MFHS": "RP",
        "HFL": "RP",
        "HFPL": "RP",
        "HFS": "HP",
    }
    wnp = {
        "ULFPL": 27,
        "LFL": 48,
        "LFPL": 48,
        "LFS": 12,
        "MFL": 432,
        "MFPL": 432,
        "MFLS": 147,
        "MFHS": 147,
        "HFL": 432,
        "HFPL": 432,
        "HFS": 469,
    }
    wpixmm = {
        "ULFPL": 21.1,
        "LFL": 16.1,
        "LFPL": 16.1,
        "LFS": 31.1,
        "MFL": 5.3,
        "MFPL": 5.3,
        "MFLS": 9.4,
        "MFHS": 9.4,
        "HFL": 5.3,
        "HFPL": 5.3,
        "HFS": 5.2,
    }
    wrhombgap = {
        "ULFPL": 0.71,
        "LFL": 0.71,
        "LFPL": 0.71,
        "LFS": 0.71,
        "MFL": 0.71,
        "MFPL": 0.71,
        "MFLS": 0.71,
        "MFHS": 0.71,
        "HFL": 0.71,
        "HFPL": 0.71,
        "HFS": 0.71,
    }
    wbd = {
        "ULFPL": ["ULFPL1"],
        "LFL": ["LFL1", "LFL2"],
        "LFPL": ["LFPL1", "LFPL2"],
        "LFS": ["LFS1", "LFS2"],
        "MFL": ["MFL1", "MFL2"],
        "MFPL": ["MFPL1", "MFPL2"],
        "MFLS": ["MFLS1", "MFLS2"],
        "MFHS": ["MFHS1", "MFHS2"],
        "HFL": ["HFL1", "HFL2"],
        "HFPL": ["HFPL1", "HFPL2"],
        "HFS": ["HFS1", "HFS2"],
    }
    windx = 0
    cardindx = 0
    for wt in wtypes:
        for ct in range(wcnt[wt]):
            wn = "{:02d}".format(windx)
            wf = OrderedDict()
            wf["type"] = wt
            wf["packing"] = wpac[wt]
            wf["rhombusgap"] = wrhombgap[wt]
            wf["npixel"] = wnp[wt]
            wf["pixsize"] = wpixmm[wt]
            wf["bands"] = wbd[wt]
            wf["card"] = "{:02d}".format(cardindx)
            cardindx += 1
            wafers[wn] = wf
            windx += 1

    cnf["wafers"] = wafers

    tubes = OrderedDict()

    woff = {
        "ULFPL": 0,
        "LFL": 0,
        "LFPL": 0,
        "LFS": 0,
        "MFL": 0,
        "MFPL": 0,
        "MFLS": 0,
        "MFHS": 0,
        "HFL": 0,
        "HFPL": 0,
        "HFS": 0,
    }

    # added in tube platescale because SAT HF has a different platescale
    # Wafers are arranges in the tube with the tube platescale
    # Telescope platescale is used for spacing tubes
    ltubes = [
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "HFL",
        "HFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "HFL",
        "HFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "HFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "HFL",
        "HFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "HFL",
        "HFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "LFL",
        "MFL",
        "MFL",
        "LFL",
        "MFL",
        "MFL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "HFPL",
        "ULFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "ULFPL",
        "HFPL",
        "ULFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "ULFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "HFPL",
        "MFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "MFPL",
        "LFPL",
        "MFPL",
        "MFPL",
        "LFPL",
    ]
    ltubepos = [
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        38,
        39,
        40,
        41,
        42,
        43,
        44,
        45,
        46,
        47,
        48,
        49,
        50,
        51,
        52,
        53,
        54,
        55,
        56,
        57,
        58,
        59,
        60,
        62,
        63,
        64,
        65,
        67,
        68,
        69,
        70,
        72,
        73,
        74,
        75,
        77,
        78,
        79,
        80,
        82,
        83,
        84,
        85,
        87,
        88,
        89,
        90,
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        38,
        39,
        40,
        41,
        42,
        43,
        44,
        45,
        46,
        47,
        48,
        49,
        50,
        51,
        52,
        53,
        54,
        55,
        56,
        57,
        58,
        59,
        60,
        62,
        63,
        64,
        65,
        67,
        68,
        69,
        70,
        72,
        73,
        74,
        75,
        77,
        78,
        79,
        80,
        82,
        83,
        84,
        85,
        87,
        88,
        89,
        90,
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        31,
        32,
        33,
        34,
        35,
        36,
        37,
        38,
        39,
        40,
        41,
        42,
        43,
        44,
        45,
        46,
        47,
        48,
        49,
        50,
        51,
        52,
        53,
        54,
        55,
        56,
        57,
        58,
        59,
        60,
        62,
        63,
        64,
        65,
        67,
        68,
        69,
        70,
        72,
        73,
        74,
        75,
        77,
        78,
        79,
        80,
        82,
        83,
        84,
        85,
        87,
        88,
        89,
        90
    ]
    # tindx is the tube number we have 85*3=255
    for tindx in range(255):
        nm = "LT{:d}".format(tindx)
        ttyp = ltubes[tindx]
        tb = OrderedDict()
        tb["type"] = ttyp
        tb["waferspace"] = 127.89
        tb["wafers"] = list()
        # tw is the wafer number in the tube. Here we use 6 for the 3 full and 3 partial wafers/tube
        for tw in range(1):
            off = 0
            for w, props in cnf["wafers"].items():
                if props["type"] == ttyp:
                    if off == woff[ttyp]:
                        tb["wafers"].append(w)
                        woff[ttyp] += 1
                        break
                    off += 1
        tb["platescale"] = 0.0047619
        tb["location"] = ltubepos[tindx]
        tubes[nm] = tb

    stubes = [
        "MFLS",
        "MFHS",
        "HFS",
        "MFLS",
        "MFHS",
        "HFS",
        "MFLS",
        "MFHS",
        "HFS",
        "MFLS",
        "MFHS",
        "HFS",
        "MFLS",
        "MFHS",
        "LFS",
        "MFLS",
        "MFHS",
        "LFS",
    ]
    stubepos = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    for tindx in range(18):
        nm = "ST{:d}".format(tindx)
        ttyp = stubes[tindx]
        tb = OrderedDict()
        tb["type"] = ttyp
        tb["waferspace"] = 127.89
        tb["wafers"] = list()
        # HF tubes have 8 full wafers + 2 partial, all others 11+2
        if ttyp == "HFS":
            for tw in range(12):
                off = 0
                for w, props in cnf["wafers"].items():
                    if props["type"] == ttyp:
                        if off == woff[ttyp]:
                            tb["wafers"].append(w)
                            woff[ttyp] += 1
                            break
                        off += 1
            # 35.0/(3*127.89)
            tb["platescale"] = 0.091224
        else:
            for tw in range(12):
                off = 0
                for w, props in cnf["wafers"].items():
                    if props["type"] == ttyp:
                        if off == woff[ttyp]:
                            tb["wafers"].append(w)
                            woff[ttyp] += 1
                            break
                        off += 1
            # 29.0/(4*127.89)
            tb["platescale"] = 0.056689
        tb["location"] = stubepos[tindx]
        tubes[nm] = tb

    cnf["tubes"] = tubes

    telescopes = OrderedDict()

    lfwhm = OrderedDict()
    lfwhm["ULFPL1"] = 10.0
    lfwhm["LFL1"] = 7.4
    lfwhm["LFPL1"] = 7.4
    lfwhm["LFL2"] = 5.1
    lfwhm["LFPL2"] = 5.1
    lfwhm["MFL1"] = 2.2
    lfwhm["MFPL1"] = 2.2
    lfwhm["MFL2"] = 1.4
    lfwhm["MFPL2"] = 1.4
    lfwhm["HFL1"] = 1.0
    lfwhm["HFPL1"] = 1.0
    lfwhm["HFL2"] = 0.9
    lfwhm["HFPL2"] = 0.9

    tele = OrderedDict()
    tele["tubes"] = [
        "LT0",
        "LT1",
        "LT2",
        "LT3",
        "LT4",
        "LT5",
        "LT6",
        "LT7",
        "LT8",
        "LT9",
        "LT10",
        "LT11",
        "LT12",
        "LT13",
        "LT14",
        "LT15",
        "LT16",
        "LT17",
        "LT18",
        "LT19",
        "LT20",
        "LT21",
        "LT22",
        "LT23",
        "LT24",
        "LT25",
        "LT26",
        "LT27",
        "LT28",
        "LT29",
        "LT30",
        "LT31",
        "LT32",
        "LT33",
        "LT34",
        "LT35",
        "LT36",
        "LT37",
        "LT38",
        "LT39",
        "LT40",
        "LT41",
        "LT42",
        "LT43",
        "LT44",
        "LT45",
        "LT46",
        "LT47",
        "LT48",
        "LT49",
        "LT50",
        "LT51",
        "LT52",
        "LT53",
        "LT54",
        "LT55",
        "LT56",
        "LT57",
        "LT58",
        "LT59",
        "LT60",
        "LT61",
        "LT62",
        "LT63",
        "LT64",
        "LT65",
        "LT66",
        "LT67",
        "LT68",
        "LT69",
        "LT70",
        "LT71",
        "LT72",
        "LT73",
        "LT74",
        "LT75",
        "LT76",
        "LT77",
        "LT78",
        "LT79",
        "LT80",
        "LT81",
        "LT82",
        "LT83",
        "LT84"
    ]
    tele["platescale"] = 0.0047619
    # This tube spacing in mm corresponds to 0.83 degrees projected on
    # the sky at a plate scale of 210 mm/deg or 0.0047619 deg/mm
    # The physical tube spacing is actually 210 mm.
    tele["tubespace"] =  174.3
    tele["fwhm"] = lfwhm
    telescopes["LAT0"] = tele

    tele = OrderedDict()
    tele["tubes"] = [
        "LT85",
        "LT86",
        "LT87",
        "LT88",
        "LT89",
        "LT90",
        "LT91",
        "LT92",
        "LT93",
        "LT94",
        "LT95",
        "LT96",
        "LT97",
        "LT98",
        "LT99",
        "LT100",
        "LT101",
        "LT102",
        "LT103",
        "LT104",
        "LT105",
        "LT106",
        "LT107",
        "LT108",
        "LT109",
        "LT110",
        "LT111",
        "LT112",
        "LT113",
        "LT114",
        "LT115",
        "LT116",
        "LT117",
        "LT118",
        "LT119",
        "LT120",
        "LT121",
        "LT122",
        "LT123",
        "LT124",
        "LT125",
        "LT126",
        "LT127",
        "LT128",
        "LT129",
        "LT130",
        "LT131",
        "LT132",
        "LT133",
        "LT134",
        "LT135",
        "LT136",
        "LT137",
        "LT138",
        "LT139",
        "LT140",
        "LT141",
        "LT142",
        "LT143",
        "LT144",
        "LT145",
        "LT146",
        "LT147",
        "LT148",
        "LT149",
        "LT150",
        "LT151",
        "LT152",
        "LT153",
        "LT154",
        "LT155",
        "LT156",
        "LT157",
        "LT158",
        "LT159",
        "LT160",
        "LT161",
        "LT162",
        "LT163",
        "LT164",
        "LT165",
        "LT166",
        "LT167",
        "LT168",
        "LT169"
    ]
    tele["platescale"] =  0.0047619
    # This tube spacing in mm corresponds to 0.83 degrees projected on
    # the sky at a plate scale of 210 mm/deg or 0.0047619 deg/mm
    # The physical tube spacing is actually 210 mm.
    tele["tubespace"] =  174.3
    tele["fwhm"] = lfwhm
    telescopes["LAT1"] = tele

    tele = OrderedDict()
    tele["tubes"] = [
        "LT170",
        "LT171",
        "LT172",
        "LT173",
        "LT174",
        "LT175",
        "LT176",
        "LT177",
        "LT178",
        "LT179",
        "LT180",
        "LT181",
        "LT182",
        "LT183",
        "LT184",
        "LT185",
        "LT186",
        "LT187",
        "LT188",
        "LT189",
        "LT190",
        "LT191",
        "LT192",
        "LT193",
        "LT194",
        "LT195",
        "LT196",
        "LT197",
        "LT198",
        "LT199",
        "LT200",
        "LT201",
        "LT202",
        "LT203",
        "LT204",
        "LT205",
        "LT206",
        "LT207",
        "LT208",
        "LT209",
        "LT210",
        "LT211",
        "LT212",
        "LT213",
        "LT214",
        "LT215",
        "LT216",
        "LT217",
        "LT218",
        "LT219",
        "LT220",
        "LT221",
        "LT222",
        "LT223",
        "LT224",
        "LT225",
        "LT226",
        "LT227",
        "LT228",
        "LT229",
        "LT230",
        "LT231",
        "LT232",
        "LT233",
        "LT234",
        "LT235",
        "LT236",
        "LT237",
        "LT238",
        "LT239",
        "LT240",
        "LT241",
        "LT242",
        "LT243",
        "LT244",
        "LT245",
        "LT246",
        "LT247",
        "LT248",
        "LT249",
        "LT250",
        "LT251",
        "LT252",
        "LT253",
        "LT254"
    ]
    tele["platescale"] = 0.0047619
    # This tube spacing in mm corresponds to 0.83 degrees projected on
    # the sky at a plate scale of 210 mm/deg or 0.0047619 deg/mm
    # The physical tube spacing is actually 210 mm.
    tele["tubespace"] =  174.3
    tele["fwhm"] = lfwhm
    telescopes["LAT2"] = tele

    #from the DSR
    sfwhm = OrderedDict()
    sfwhm["LFS1"] = 72.8
    sfwhm["LFS2"] = 72.8
    sfwhm["MFLS1"] = 25.5
    sfwhm["MFLS2"] = 25.5
    sfwhm["MFHS1"] = 22.7
    sfwhm["MFHS2"] = 22.7
    sfwhm["HFS1"] = 13.0
    sfwhm["HFS2"] = 13.0

    tele = OrderedDict()
    tele["tubes"] = ["ST0", "ST1", "ST2"]
    tele["platescale"] = 0.056689
    tele["tubespace"] = 700.0
    tele["fwhm"] = sfwhm
    telescopes["SAT0"] = tele

    tele = OrderedDict()
    tele["tubes"] = ["ST3", "ST4", "ST5"]
    tele["platescale"] = 0.056689
    tele["tubespace"] = 700.0
    tele["fwhm"] = sfwhm
    telescopes["SAT1"] = tele

    tele = OrderedDict()
    tele["tubes"] = ["ST6", "ST7", "ST8"]
    tele["platescale"] = 0.056689
    tele["tubespace"] = 700.0
    tele["fwhm"] = sfwhm
    telescopes["SAT2"] = tele

    tele = OrderedDict()
    tele["tubes"] = ["ST9", "ST10", "ST11"]
    tele["platescale"] = 0.056689
    tele["tubespace"] = 700.0
    tele["fwhm"] = sfwhm
    telescopes["SAT3"] = tele

    tele = OrderedDict()
    tele["tubes"] = ["ST12", "ST13", "ST14"]
    tele["platescale"] = 0.056689
    tele["tubespace"] = 700.0
    tele["fwhm"] = sfwhm
    telescopes["SAT4"] = tele

    tele = OrderedDict()
    tele["tubes"] = ["ST15", "ST16", "ST17"]
    tele["platescale"] = 0.056689
    tele["tubespace"] = 700.0
    tele["fwhm"] = sfwhm
    telescopes["SAT5"] = tele

    cnf["telescopes"] = telescopes

    cards = OrderedDict()
    crates = OrderedDict()

    crt_indx = 0

    for tel in cnf["telescopes"]:
        crn = "{:d}".format(crt_indx)
        crt = OrderedDict()
        crt["cards"] = list()
        crt["telescope"] = tel

        ## get all the wafer card numbers for a telescope
        tb_wfrs = [cnf["tubes"][t]["wafers"] for t in cnf["telescopes"][tel]["tubes"]]
        tl_wfrs = [i for sl in tb_wfrs for i in sl]
        wafer_cards = [cnf["wafers"][w]["card"] for w in tl_wfrs]

        # add all cards to the card table and assign to crates
        for crd in wafer_cards:
            cdprops = OrderedDict()
            cdprops["nbias"] = 12
            cdprops["ncoax"] = 2
            cdprops["nchannel"] = 2000
            cards[crd] = cdprops

            crt["cards"].append(crd)

            # name new crates when current one is full
            # 6 cards/crate, in future for partial wafers change card number accordingly
            if len(crt["cards"]) >= 6:
                crates[crn] = crt
                crt_indx += 1
                crn = "{:d}".format(crt_indx)
                crt = OrderedDict()
                crt["cards"] = list()
                crt["telescope"] = tel

        # each telescope starts with a new crate
        crates[crn] = crt
        crt_indx += 1

    cnf["cards"] = cards
    cnf["crates"] = crates

    pl = ["A", "B"]
    hand = ["L", "R"]

    dets = OrderedDict()
    for d in range(4):
        dprops = OrderedDict()
        dprops["wafer"] = "07"
        dprops["ID"] = d
        dprops["pixel"] = "000"
        bindx = d % 2
        dprops["band"] = "LFL{}".format(bindx)
        dprops["fwhm"] = 1.0
        dprops["pol"] = pl[bindx]
        dprops["handed"] = None
        dprops["card"] = "07"
        dprops["channel"] = d
        dprops["coax"] = 0
        dprops["bias"] = 0
        dprops["quat"] = np.array([0.0, 0.0, 0.0, 1.0])
        dname = "{}_{}_{}_{}".format("07", "000", dprops["band"], dprops["pol"])
        dets[dname] = dprops

    cnf["detectors"] = dets

    hw = Hardware()
    hw.data = cnf

    return hw
