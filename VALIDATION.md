# Validation — 2026-11-25

## Completed

- Unit/regression tests: 18 passing checks covering atom bounds, extended atoms, constant frame rate, sample indexing, source changes, RGGB rejection, unequal-green WB rejection, metadata, CFA pixel preservation, odd default crop origins, profile mismatch, overwrite protection, cancellation, resume/hash verification, output locks, Unicode filenames/reel aliases and continuous filenames after frame sampling.
- Real inputs: Nikon Z6III 4040×2276 at 30000/1001, Nikon Z8 4140×2330 at 25, Nikon Z6III 6060×3410 at 30000/1001.
- First/middle/last frame of each source: **9/9** direct SDK Bayer arrays match DNG pixels read by LibRaw exactly, and match after Adobe DNG Converter 18.7 re-import exactly.
- Frame-specific black level, white balance, RGGB pattern and reference WhiteLevel checked independently in LibRaw. Profile tags checked against the supplied reference DNG during every write.
- Color path: the same first-frame CFA was placed in the validated same-camera NEF container and converted by Adobe to reference DNG. The reference and the direct CinemaDNG were then rendered through identical LibRaw linear demosaic, camera WB, sRGB and +4 EV settings. After aligning the source CFA region and excluding a 24-pixel demosaic border, all three comparisons had **maximum difference 0 / 65535**. A contact sheet was visually inspected. This validates preservation of the selected camera-profile path, not absolute colorimetry or Nikon/RED look equivalence.
- ExifTool recognizes FrameRate, ReelName, color matrix, AsShotNeutral and default crop. Private input paths, original recordings and camera profiles are not committed.

## Not claimed

- No DaVinci Resolve, Premiere or Lightroom GUI acceptance test on this machine; those applications were not installed for this task.
- No measured color-chart/illuminant calibration, gamut accuracy claim, or guarantee for every camera/firmware/recording mode.
- No source audio, source SMPTE timecode, lens-geometry correction, camera picture-control emulation or independent compression decoder.
- Saturation is read from the supplied reference DNG. It is a profile/input choice, not an assertion that all video modes share the photographic saturation level.

## Windows interface and portable build

- Launched the packaged GUI, inspected its default-size layout, filled the four path fields, ran the inspection action and converted three real Z8 frames using the actual Start button. The visible status reported three completed and verified frames; the output files and receipts were independently checked afterward.
- Packaged console worker converted a two-frame, step-2 selection into a Chinese/space-containing output path, with continuous frame filenames and 25/2 fps metadata; a second invocation verified and resumed the same job successfully.
- Per-frame standard EXIF exposure, aperture, focal length and ISO were independently read from final output. The application uses a hidden worker process; closing/stopping requests finish the current frame before exit.
