"""GST verification behind one interface (TRUST_PLAN §4, phase T2).

Project-agnostic: adapter, providers, router, cache, budget and link import nothing from
cosmetics, so msme/gst.py can be replaced by this. `service.py` is the cosmetics glue.
Only a `live_gstn` record can ever make a firm "GST verified"; the stub provider exists to
exercise the plumbing before a vendor is chosen, and its records verify nothing.
"""
