The build the payloads in data/scrpatches.*.json are written against.

repair_scrpatches.py rewrites them from this build onto a target build, so this
is the only correct --old for that step -- not "the build before the new one",
which is what the rest of the update chain means by old. They are different
numbers as soon as more than one game update has passed, and using the wrong
one migrates addresses that were never there.

The .ysa sources under scrasm/customfuncs/src/ are authored against this build
too; build_customfuncs.py assembles against it.
