"""Routing spike (plan item 3.2): Tesseract on the path from a site's records to the fabric.

A spike that informs decision 1 (where Tesseract runs at a site) and decision 2 (what Tesseract routes when nobody
asks a question). It decides neither. The design and the pre-registered criterion are in
``docs/collective/ROUTING-SPIKE.md``.

Modules, by side:

* ``wire`` (both sides): the narrow request/response interface between HQ and a site, kinds ``describe`` and
  ``question``; an in-process endpoint and a child-process endpoint pass the same bytes. Standard library only.
* ``site_process`` (site): a site's NeuralGraph of its own records, Tesseract over it through
  ``NeuralGraphMemoryAdapter``, the shipped lexical judge and verdict rules, and the site's own Boundary. Also
  ``python -m research.routing_spike.site_process`` for process mode.
* ``hq`` (HQ): the cell view, the routers, one ``SiteProxyAdapter`` per site, ``TesseractCoordinator`` and the shipped
  gate. It never imports ``site_process``, numpy or the retrieval package.
* ``publish`` (HQ): verdicts and conclusions into an in-process fabric service, with lineage.
* ``world``, ``score``, ``run`` (harness): worlds, scoring and the CLI.

Nothing in ``mycelic/`` imports this package. Everything here is synthetic and offline.
"""
