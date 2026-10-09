# Patches on top of the active implementation

These are the overnight commits made on top of `claude/mycelic-implementation-vr034p` at `f96f2632e52e4b050fed080ba5c9f9beca0a6894`
(the active Mycelic implementation). The designated branch `claude/friendly-mayer-y9f1vt` is based on `main`,
whose `mycelic/` package is a different, incompatible system, and rewriting the designated branch onto the
implementation was refused by the session's safety settings. To reproduce the tested code:

    git fetch origin claude/mycelic-implementation-vr034p
    git checkout -b e2e-overnight f96f2632e52e4b050fed080ba5c9f9beca0a6894
    git am research/mycelic_e2e/impl_patches/*.patch      # run from a checkout that has this directory

Tested head after applying: 56f98327a5864b0d96f615edaf1a2660dc1b00f7
