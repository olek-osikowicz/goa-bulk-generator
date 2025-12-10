import logging as log
import urllib
import zipfile
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd
import requests
import tqdm
from geopandas import GeoDataFrame
from shapely.geometry import box, shape

# CONSTANS

MERCATOR_CRS = "EPSG:3857"
GEO_2D_CRS = "EPSG:4326"

CACHE_DIR = Path("cache")
SEA_WATER_POLYGONS_PATH = CACHE_DIR / "water-polygons-split-4326" / "water_polygons.shx"

WATER_TAGS = {"natural": ["water", "bay"]}


class Fetcher:
    def __init__(self, bbox_cords: list, map_space_dims: list) -> None:
        # Process bbox
        self.bbox_cords = bbox_cords
        log.debug(f"{bbox_cords = }")
        self.bbox_pol = box(*bbox_cords)
        self.bbox_gdf = GeoDataFrame(geometry=[self.bbox_pol], crs=GEO_2D_CRS).to_crs(
            MERCATOR_CRS
        )

        self.mercator_bbox = self.bbox_gdf.total_bounds
        self.centroid_mercator = self.bbox_gdf.geometry.centroid.iloc[0]

        self.map_space_dims = map_space_dims
        self.set_scale()

    @staticmethod
    def ensure_water_polygons():
        if SEA_WATER_POLYGONS_PATH.exists():
            return
        log.warning("Sea water polygons not found, downloading...")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        url = "https://osmdata.openstreetmap.de/download/water-polygons-split-4326.zip"

        def update_hook(blocknum, blocksize, totalsize):
            if progress_bar.total is None and totalsize > 0:
                progress_bar.total = totalsize
            progress_bar.update(blocksize)

        tmp_zip_path = CACHE_DIR / "water-polygons-split-4326.zip"
        if not tmp_zip_path.exists():
            progress_bar = tqdm.tqdm(
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc="Downloading sea water polygons",
            )

            urllib.request.urlretrieve(url, tmp_zip_path, update_hook)

        log.warning("Extracting sea water polygons...")
        with zipfile.ZipFile(tmp_zip_path, "r") as zip_ref:
            zip_ref.extractall(CACHE_DIR)

        tmp_zip_path.unlink()
        log.warning("Sea water polygons ready.")

    def mergeGeometries(self, gdf: GeoDataFrame):
        shape = gdf.geometry.unary_union
        if shape:
            gdf = gpd.GeoDataFrame(geometry=[shape], crs=GEO_2D_CRS)
            gdf = gdf.explode(index_parts=False)

        return gdf

    def set_scale(self):
        bounds = self.mercator_bbox
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]

        # hole-width
        s1 = self.map_space_dims[2] / width

        # hole-heigh
        s2 = self.map_space_dims[3] / height
        self.s = max(s1, s2)

    def transformGDF(self, gdf: GeoDataFrame):
        gdf = gdf.reset_index().clip_by_rect(*self.bbox_cords).explode(index_parts=False)

        gdf = gdf[gdf.geom_type == "Polygon"]

        # merge polygons
        gdf = self.mergeGeometries(gdf)
        gdf = gdf.to_crs(MERCATOR_CRS)

        return gdf

    def get_osmGDF(self, tags, scale=True):
        try:
            osm_gdf = ox.features_from_polygon(self.bbox_pol, tags=tags)
        except Exception:
            # return empty geometry if something goes wrong
            return gpd.GeoSeries([])

        osm_gdf = self.transformGDF(osm_gdf)

        if scale:
            osm_gdf = self.scaleToPoster(osm_gdf)
        return osm_gdf

    def get_f1GDF(self, selector):
        # circut=f'wr["name"="{name}"];'
        url = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
        query = f"""[out:json];
            {selector}
            convert item ::=::,::geom=geom(),_osm_type=type();
            out geom;"""
        response = requests.get(url, params={"data": query})
        data = response.json()
        results_dict = [
            {
                "geometry": shape(element["geometry"]),
            }
            for element in data["elements"]
        ]

        gdf = gpd.GeoDataFrame(results_dict)
        gdf = gdf.reset_index()[["geometry"]]
        gdf = gdf.explode(index_parts=False)
        gdf = gdf[gdf.geom_type == "LineString"]
        gdf = gdf.drop_duplicates()
        gdf = gdf.set_crs("EPSG:4326")
        gdf = gdf.to_crs("EPSG:3857")
        gdf = self.scaleToPoster(gdf)

        return gdf

    def scaleToPoster(self, gdf):
        # center of map canvas
        map_space_center_x = self.map_space_dims[0] + self.map_space_dims[2] / 2
        map_space_center_y = self.map_space_dims[1] + self.map_space_dims[3] / 2
        log.debug(f"{map_space_center_x = }, {map_space_center_y = }")

        if not gdf.geometry.empty:
            gdf["geometry"] = (
                gdf["geometry"]
                .translate(xoff=-self.centroid_mercator.x, yoff=-self.centroid_mercator.y)
                # TODO maybe 2 next lines should be in one operation for performance boost
                # inverse Y- axis
                .scale(xfact=1, yfact=-1, zfact=1.0, origin=(0, 0))
                # scale to fit poster
                .scale(xfact=self.s, yfact=self.s, zfact=1.0, origin=(0, 0))
                # shift to poster center
                .translate(xoff=map_space_center_x, yoff=map_space_center_y)
            )

        return gdf

    def get_waterGDF(self):
        log.debug("Retrieving sea water polygons")
        sea_water_gdf = gpd.read_file(SEA_WATER_POLYGONS_PATH, bbox=self.bbox_pol)
        log.debug("Sea water polygons retrieved")
        sea_water_gdf = self.transformGDF(sea_water_gdf)
        log.debug("Sea water transformed")

        # no scaling as we are not done transforming yet
        inland_water_gdf = self.get_osmGDF(WATER_TAGS, scale=False)
        log.debug("Inland water retrieved")

        if inland_water_gdf.empty:
            gdf = sea_water_gdf
        else:
            gdf = pd.concat([inland_water_gdf, sea_water_gdf])

        log.debug("Appended")
        gdf = self.mergeGeometries(gdf)
        log.debug("Water merged")

        gdf = self.scaleToPoster(gdf)
        log.debug("Scaled to poster")

        return gdf

    def get_streetsGDF(self, street_types: list):
        tags = {"highway": street_types}
        gdf = ox.features_from_polygon(self.bbox_pol, tags=tags)

        def unpack_lists(highway_type):
            if isinstance(highway_type, str):
                return highway_type

            return highway_type[0]

        gdf["highway"] = gdf["highway"].apply(unpack_lists)

        gdf = gdf.reset_index()[["highway", "geometry"]]
        gdf = gdf.explode(index_parts=False)
        gdf = gdf[gdf.geom_type == "LineString"]
        gdf = gdf.drop_duplicates()
        # .clip_by_rect(*self.bbox_cords)
        gdf = gdf.rename(columns={"highway": "way_type"})
        gdf = gdf.to_crs(MERCATOR_CRS)
        gdf = self.scaleToPoster(gdf)

        return gdf


if __name__ == "__main__":
    pass
