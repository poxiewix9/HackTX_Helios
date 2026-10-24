"""ARCO benchmark harness (Phase 5).

Pipeline A (`fp32_full`): frame -> JPEG (what shipping a frame would cost) -> unsplit FP32
model -> postprocess -> georef, all in one process.
Pipeline B (`int8_split`): frame -> INT8 head -> feature packet -> decode -> FP32 tail ->
postprocess -> georef, the production topology, run in one process so the two are
measured on identical frames with identical clocks.

Every number is measured with `time.perf_counter` on the machine described by
`HardwareInfo` (read from the OS, never typed in). Recorded runs replay with
`mode="recorded"`.
"""

__version__ = "0.1.0"
