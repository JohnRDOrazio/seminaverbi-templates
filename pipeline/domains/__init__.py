"""The article domains the pipeline knows."""
from pipeline.domains import architecture, art, film, literature, music

# in this order: the press gives an item of several classes to the first domain that owns one (press.link.class_route)
DOMAINS = {"film": film, "literature": literature, "music": music, "art": art, "architecture": architecture}
