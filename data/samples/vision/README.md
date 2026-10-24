# Vision sample assets

- `georef/camera_front_st.json` — `CameraGeoref` for a **hypothetical** street camera near
  Front St / Lahainaluna Rd, Lahaina (approximate position, 6 m mast, 14° down-pitch,
  70° FOV). The six ground-plane correspondences were generated from that pinhole model,
  not surveyed. Used by the edge node (`ARCO_EDGE_GEOREF_PATH`) and by tests.
- `georef/tile_lahaina_0.json` — `TileGeoref` for a 1024×1024 synthetic macro tile covering
  part of the Lahaina sample bbox (affine = GDAL style, north-up).
- The scripted detections for `ARCO_DETECTOR=synthetic` live in
  `services/base-station/config/synthetic.yaml` (override with
  `ARCO_SYNTHETIC_SCHEDULE_PATH`); they reference the camera above by `source_id`.

Everything here is labelled "Simulated on real event geometry": the geometry is real
Lahaina coordinates, the cameras/tiles/detections are not.
