"""The article domains the pipeline knows."""
from pipeline.domains import architecture, art, film, literature, music

# in this order: the press gives an item of several classes to the first domain that owns one (press.link.class_route)
DOMAINS = {"film": film, "literature": literature, "music": music, "art": art, "architecture": architecture}


def hold_benchmarks(queue, cfg: dict) -> None:
    """Hold back the benchmark works of every domain that has one. Called before anything acts on live rows and after
    the press adds some: the press brings a work in without its domain's `candidates`, which holds them too."""
    for domain in DOMAINS.values():
        hold = getattr(domain, "hold_benchmark", None)
        if hold is not None:
            hold(queue, cfg)
