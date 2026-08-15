"""Ruida command registry assembled from independently observed streams."""

from __future__ import annotations

from dataclasses import replace

from .fields import (
    AbsoluteMmField,
    ByteField,
    BytesField,
    ColorField,
    CStringField,
    PackedBytes8Field,
    PowerField,
    RelativeMmField,
    ScaledU35Field,
    U14Field,
    U35Field,
)
from .specs import CommandRegistry, CommandSpec


def _spec(code: str, name: str, *fields) -> CommandSpec:
    return CommandSpec(bytes.fromhex(code), name, fields)


SRC_LIGHTBURN = "local:lightburn-2.1.03-fixtures"
SRC_LIGHTBURN_CAPABILITIES = (
    "local:lightburn-2.1.03-capability-fixtures"
)
SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1 = (
    "local:hardware-ruida-644xs-usb-serial-v1"
)
SRC_HARDWARE_BOSS_LS2040_ADDRESS_512_ZERO_V1 = (
    "local:hardware-boss-ls2040-usb-serial-address-512-zero-v1"
)
SRC_MEERK40T = (
    "github:meerk40t/meerk40t@"
    "5f68a45bff41d98e4d3fe8b8267857218099afa8"
)
SRC_RUIDA_LASER = (
    "github:jnweiger/ruida-laser@"
    "a1e7b9b93b10d5cac79c875bc3efec46f7397a11"
)
SRC_RUIDA_PA = (
    "github:StevenIsaacs/ruida-pa@"
    "92efde98004d9948474eb712ef6f5b164f468c4f"
)
SRC_LIBLASERCUT = (
    "github:t-oster/LibLaserCut@"
    "ebe72ea3af3b2ab52d797d8100c635f68722100e"
)
CATALOG_SOURCES = (
    SRC_HARDWARE_BOSS_LS2040_ADDRESS_512_ZERO_V1,
    SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
    SRC_LIGHTBURN,
    SRC_LIGHTBURN_CAPABILITIES,
    SRC_LIBLASERCUT,
    SRC_MEERK40T,
    SRC_RUIDA_LASER,
    SRC_RUIDA_PA,
)
HARDWARE_SETTING_SOURCES = (
    SRC_HARDWARE_RUIDA_644XS_USB_SERIAL_V1,
    SRC_RUIDA_PA,
    SRC_MEERK40T,
)
HARDWARE_SETTING_SHAPE_SOURCES = (
    SRC_HARDWARE_BOSS_LS2040_ADDRESS_512_ZERO_V1,
    *HARDWARE_SETTING_SOURCES,
)
HARDWARE_GET_SETTING_NOTES = (
    "One supervised USB serial capture from a controller configured as a "
    "Ruida 644XS observed that a DA00 request for address 5 produced a "
    "numeric DA01 reply with matching address 5 and value 300000. The "
    "stream used magic 0x88 without checksum framing or a separate ACK. "
    "A separate Boss LS2040 USB-serial capture observed exact logical "
    "DA000400 and a matching nine-byte DA01 reply whose value was zero. "
    "That second capture confirms only the correlated address-512 exchange "
    "shape and zero value at one unspecified instant. It does not establish "
    "that address 512 means machine status, that zero means idle, any status "
    "flag meaning, controller completion, or a physical side effect. These "
    "captures do not establish behavior for every address, controller model, "
    "or firmware dialect."
)
HARDWARE_SETTING_REPLY_NOTES = (
    "One supervised USB serial capture from a controller configured as a "
    "Ruida 644XS returned address 5 and numeric value 300000 after DA00 "
    "address 5. The stream used magic 0x88 without checksum framing or a "
    "separate ACK. This confirms only that reply shape on the captured "
    "setup. A separate Boss LS2040 USB-serial capture returned matching "
    "address 512 and numeric value zero in the same nine-byte reply shape. "
    "It confirms only that exact reply at one unspecified instant, not the "
    "address or value semantics, controller completion, or physical state."
)
REPORTED_PROCESS_STOP_NOTES = (
    "Pinned MeerK40t defines logical D8 01 as process stop and emits it "
    "from its Ruida abort path on the normal controller command channel. "
    "This is implementation-reported evidence, not a hardware observation "
    "or proof that controller execution has stopped."
)


SPECS = (
    _spec("8003", "z_offset_delta", AbsoluteMmField("delta_mm")),
    *(
        _spec(code, name, AbsoluteMmField("position_mm"))
        for code, name in (
            ("8000", "move_far_x"),
            ("8008", "move_far_z_reported"),
            ("a000", "move_far_y_reported"),
            ("a008", "move_far_u_alt"),
        )
    ),
    _spec(
        "88",
        "move_absolute",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec(
        "89",
        "move_relative",
        RelativeMmField("dx_mm"),
        RelativeMmField("dy_mm"),
    ),
    _spec("8a", "move_horizontal", RelativeMmField("dx_mm")),
    _spec("8b", "move_vertical", RelativeMmField("dy_mm")),
    _spec(
        "a8",
        "cut_absolute",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec(
        "a9",
        "cut_relative",
        RelativeMmField("dx_mm"),
        RelativeMmField("dy_mm"),
    ),
    _spec("aa", "cut_horizontal", RelativeMmField("dx_mm")),
    _spec("ab", "cut_vertical", RelativeMmField("dy_mm")),
    _spec("a550", "interface_key_down", ByteField("key")),
    _spec("a551", "interface_key_up", ByteField("key")),
    _spec("a553", "interface_frame", ByteField("value")),
    _spec("a750", "keypress", ByteField("key")),
    _spec("a751", "keyrelease", ByteField("key")),
    _spec("a75300", "keypress_interface_frame"),
    *(
        _spec(code, name, PowerField("power_percent"))
        for code, name in (
            ("c0", "immediate_power_2"),
            ("c1", "end_power_2"),
            ("c2", "immediate_power_3"),
            ("c3", "immediate_power_4"),
            ("c4", "end_power_3"),
            ("c5", "end_power_4"),
            ("c7", "immediate_power_1"),
            ("c8", "end_power_1"),
        )
    ),
    _spec("c601", "laser_1_min_power", PowerField("power_percent")),
    _spec("c602", "laser_1_max_power", PowerField("power_percent")),
    _spec("c605", "laser_3_min_power", PowerField("power_percent")),
    _spec("c606", "laser_3_max_power", PowerField("power_percent")),
    _spec("c607", "laser_4_min_power", PowerField("power_percent")),
    _spec("c608", "laser_4_max_power", PowerField("power_percent")),
    _spec("c610", "laser_interval", ScaledU35Field("time_ms")),
    _spec("c611", "additional_delay", ScaledU35Field("time_ms")),
    _spec("c612", "laser_on_delay", ScaledU35Field("time_ms")),
    _spec("c613", "laser_off_delay", ScaledU35Field("time_ms")),
    _spec("c615", "delay_set_2_on", ScaledU35Field("time_ms")),
    _spec("c616", "delay_set_2_off", ScaledU35Field("time_ms")),
    _spec("c621", "laser_2_min_power", PowerField("power_percent")),
    _spec("c622", "laser_2_max_power", PowerField("power_percent")),
    _spec(
        "c631",
        "layer_laser_1_min_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec(
        "c632",
        "layer_laser_1_max_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec(
        "c635",
        "layer_laser_3_min_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec(
        "c636",
        "layer_laser_3_max_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec(
        "c637",
        "layer_laser_4_min_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec(
        "c638",
        "layer_laser_4_max_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec(
        "c641",
        "layer_laser_2_min_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec(
        "c642",
        "layer_laser_2_max_power",
        ByteField("layer"),
        PowerField("power_percent"),
    ),
    _spec("c650", "through_power_1", PowerField("power_percent")),
    _spec("c651", "through_power_2", PowerField("power_percent")),
    _spec("c655", "through_power_3", PowerField("power_percent")),
    _spec("c656", "through_power_4", PowerField("power_percent")),
    _spec(
        "c660",
        "layer_frequency",
        ByteField("laser"),
        ByteField("layer"),
        ScaledU35Field("frequency_khz"),
    ),
    _spec(
        "c666",
        "layer_fiber_pulse_width",
        ByteField("selector_a"),
        ByteField("selector_b"),
        U35Field("pulse_width_ns"),
    ),
    _spec("c902", "active_speed", ScaledU35Field("speed_mm_s")),
    _spec("c903", "axis_speed", ScaledU35Field("speed_mm_s")),
    _spec(
        "c904",
        "layer_speed",
        ByteField("layer"),
        ScaledU35Field("speed_mm_s"),
    ),
    _spec("c905", "forced_engrave_speed", ScaledU35Field("speed_mm_s")),
    _spec("c906", "axis_move_speed", ScaledU35Field("speed_mm_s")),
    _spec("ca01", "layer_control", ByteField("operation")),
    _spec("ca02", "select_layer", ByteField("layer")),
    _spec("ca03", "enable_laser_tube_start", ByteField("enabled")),
    _spec("ca04", "x_sign_map", ByteField("value")),
    _spec("ca05", "default_color", ColorField("color_rgb")),
    _spec(
        "ca06",
        "layer_color",
        ByteField("layer"),
        ColorField("color_rgb"),
    ),
    _spec("ca10", "external_io", ByteField("value")),
    _spec("ca22", "layer_count", ByteField("count_minus_one")),
    _spec("ca30", "u_file_id", U14Field("identifier")),
    _spec("ca40", "zu_map", ByteField("value")),
    _spec(
        "ca41",
        "layer_mode_or_attributes",
        ByteField("layer"),
        ByteField("value"),
    ),
    _spec("d7", "end_of_file"),
    _spec("d800", "process_start"),
    _spec("d801", "process_stop"),
    _spec("d802", "process_pause"),
    _spec("d803", "process_resume"),
    _spec("d810", "reference_absolute"),
    _spec("d811", "reference_anchor"),
    _spec("d812", "reference_current"),
    *(
        _spec(code, name)
        for code, name in (
            ("d820", "keydown_x_left"),
            ("d821", "keydown_x_right"),
            ("d822", "keydown_y_top"),
            ("d823", "keydown_y_bottom"),
            ("d824", "keydown_z_up"),
            ("d825", "keydown_z_down"),
            ("d826", "keydown_u_forward"),
            ("d827", "keydown_u_backwards"),
            ("d82a", "home_xy"),
            ("d82c", "home_z"),
            ("d82d", "home_u"),
            ("d82e", "focus_z"),
            ("d830", "keyup_x_left"),
            ("d831", "keyup_x_right"),
            ("d832", "keyup_y_top"),
            ("d833", "keyup_y_bottom"),
            ("d834", "keyup_z_up"),
            ("d835", "keyup_z_down"),
            ("d836", "keyup_u_forward"),
            ("d837", "keyup_u_backwards"),
            ("d838", "keyup_unknown_20"),
            ("d839", "home_a"),
            ("d83a", "home_b"),
            ("d83b", "home_c"),
            ("d83c", "home_d"),
            ("d840", "keydown_unknown_18"),
            ("d841", "keydown_unknown_19"),
            ("d842", "keydown_unknown_1a"),
            ("d843", "keydown_unknown_1b"),
            ("d844", "keydown_unknown_1c"),
            ("d845", "keydown_unknown_1d"),
            ("d846", "keydown_unknown_1e"),
            ("d847", "keydown_unknown_1f"),
            ("d848", "keyup_unknown_08"),
            ("d849", "keyup_unknown_09"),
            ("d84a", "keyup_unknown_0a"),
            ("d84b", "keyup_unknown_0b"),
            ("d84c", "keyup_unknown_0c"),
            ("d84d", "keyup_unknown_0d"),
            ("d84e", "keyup_unknown_0e"),
            ("d84f", "keyup_unknown_0f"),
            ("d851", "inhale_toggle"),
        )
    ),
    _spec(
        "d900",
        "direct_move_x",
        ByteField("mode"),
        AbsoluteMmField("distance_mm"),
    ),
    _spec(
        "d901",
        "direct_move_y",
        ByteField("mode"),
        AbsoluteMmField("distance_mm"),
    ),
    _spec(
        "d902",
        "direct_move_z",
        ByteField("mode"),
        AbsoluteMmField("distance_mm"),
    ),
    _spec(
        "d903",
        "direct_move_u",
        ByteField("relation"),
        AbsoluteMmField("distance_mm"),
    ),
    _spec("d90f", "jog_feed_axis", ByteField("relation")),
    _spec(
        "d910",
        "direct_move_xy",
        ByteField("relation"),
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec(
        "d930",
        "direct_move_xyu",
        ByteField("relation"),
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
        AbsoluteMmField("u_mm"),
    ),
    _spec("da00", "get_setting", U14Field("address")),
    _spec(
        "da01",
        "set_setting",
        U14Field("address"),
        U35Field("first_value"),
        U35Field("second_value"),
    ),
    _spec(
        "da05",
        "get_indexed_value",
        U14Field("index"),
        U35Field("value"),
    ),
    _spec(
        "e500",
        "document_file_upload",
        U14Field("file_number"),
        U35Field("first_value"),
        U35Field("second_value"),
    ),
    _spec("e502", "document_file_end"),
    _spec("e505", "file_checksum", U35Field("value")),
    _spec("e601", "set_absolute"),
    _spec("e700", "block_end"),
    _spec("e701", "set_filename", CStringField("filename")),
    _spec(
        "e703",
        "job_min_point",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec(
        "e704",
        "job_copies",
        U14Field("columns"),
        U14Field("rows"),
        AbsoluteMmField("x_step_mm"),
        AbsoluteMmField("y_step_mm"),
    ),
    _spec("e705", "array_direction", ByteField("direction")),
    _spec(
        "e706",
        "feed_repeat",
        U35Field("first_value"),
        U35Field("second_value"),
    ),
    _spec(
        "e707",
        "job_max_point",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec(
        "e708",
        "array_copies",
        U14Field("columns"),
        U14Field("rows"),
        AbsoluteMmField("x_step_mm"),
        AbsoluteMmField("y_step_mm"),
    ),
    _spec("e709", "feed_length", U35Field("value")),
    _spec("e70a", "feed_info", U35Field("value")),
    _spec("e70b", "array_mirror_cut", ByteField("enabled")),
    *(
        _spec(
            code,
            name,
            AbsoluteMmField("x_mm"),
            AbsoluteMmField("y_mm"),
        )
        for code, name in (
            ("e713", "array_min_point"),
            ("e717", "array_max_point"),
            ("e723", "array_add"),
            ("e737", "array_even_distance"),
            ("e750", "document_min_point"),
            ("e751", "document_max_point"),
        )
    ),
    _spec("e724", "array_mirror", ByteField("value")),
    _spec(
        "e732",
        "rdworks_extension",
        U35Field("first_value"),
        U35Field("second_value"),
    ),
    _spec(
        "e735",
        "block_size",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec("e736", "set_file_empty", ByteField("value")),
    _spec("e738", "feed_auto_pause", ByteField("enabled")),
    _spec("e73a", "union_block_property"),
    *(
        _spec(
            code,
            name,
            ByteField("layer"),
            AbsoluteMmField("x_mm"),
            AbsoluteMmField("y_mm"),
        )
        for code, name in (
            ("e752", "layer_min_point"),
            ("e753", "layer_max_point"),
            ("e761", "layer_extended_min_point"),
            ("e762", "layer_extended_max_point"),
        )
    ),
    _spec(
        "e754",
        "pen_offset_axis",
        ByteField("axis"),
        AbsoluteMmField("offset_mm"),
    ),
    _spec(
        "e755",
        "layer_offset_axis",
        ByteField("axis"),
        AbsoluteMmField("offset_mm"),
    ),
    _spec("e760", "current_element_index", ByteField("index")),
    _spec(
        "e800",
        "delete_document",
        U35Field("first_value"),
        U35Field("second_value"),
    ),
    _spec("e801", "document_number", U14Field("number")),
    _spec("e802", "file_transfer"),
    _spec("e803", "select_document", ByteField("number")),
    _spec("e804", "calculate_document_time"),
    _spec("ea", "array_start", ByteField("value")),
    _spec("eb", "array_end"),
    _spec("f0", "reference_point_set"),
    _spec("f100", "element_max_index", ByteField("index")),
    _spec("f101", "element_name_max_index", ByteField("index")),
    _spec("f102", "enable_block_cutting", ByteField("enabled")),
    _spec(
        "f103",
        "display_offset",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec("f104", "feed_auto_calculate", ByteField("enabled")),
    _spec("f200", "element_index", ByteField("index")),
    _spec("f201", "element_name_index", ByteField("index")),
    _spec("f202", "element_name", PackedBytes8Field("name_bytes")),
    _spec(
        "f203",
        "element_array_min_point",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec(
        "f204",
        "element_array_max_point",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec(
        "f205",
        "element_copies",
        U14Field("columns"),
        U14Field("rows"),
        AbsoluteMmField("x_step_mm"),
        AbsoluteMmField("y_step_mm"),
    ),
    _spec(
        "f206",
        "element_array_add",
        AbsoluteMmField("x_mm"),
        AbsoluteMmField("y_mm"),
    ),
    _spec("f207", "element_array_mirror", ByteField("value")),
)


LIGHTBURN_OBSERVED = {
    "8003",
    "88",
    "89",
    "8a",
    "8b",
    "a8",
    "a9",
    "aa",
    "ab",
    "c2",
    "c601",
    "c602",
    "c610",
    "c611",
    "c612",
    "c613",
    "c621",
    "c622",
    "c631",
    "c632",
    "c641",
    "c642",
    "c650",
    "c651",
    "c660",
    "c666",
    "c7",
    "c902",
    "c903",
    "c904",
    "ca01",
    "ca02",
    "ca03",
    "ca06",
    "ca10",
    "ca22",
    "ca41",
    "d7",
    "d800",
    "d810",
    "da01",
    "e505",
    "e601",
    "e700",
    "e703",
    "e704",
    "e705",
    "e706",
    "e707",
    "e708",
    "e713",
    "e717",
    "e723",
    "e724",
    "e737",
    "e738",
    "e750",
    "e751",
    "e752",
    "e753",
    "e754",
    "e755",
    "e760",
    "e761",
    "e762",
    "ea",
    "eb",
    "f0",
    "f100",
    "f101",
    "f102",
    "f103",
    "f200",
    "f201",
    "f202",
    "f203",
    "f204",
    "f205",
    "f206",
    "f207",
}


PROVISIONAL = {
    "d90f",
    "da05",
    "e732",
    "e800",
    "e803",
    "e804",
}


PROVISIONAL_NOTES = {
    "d90f": "Reported payload length differs across implementations.",
    "da05": "Request and reply layouts differ across implementations.",
    "e732": "One or two five-group values are reported.",
    "e800": "Five-group and two-group file identifiers are reported.",
    "e803": "One-byte and two-byte file identifiers are reported.",
    "e804": "Both empty and two-byte payloads are reported.",
}


CONTROLLED_SEMANTICS = {
    "88",
    "89",
    "8a",
    "8b",
    "a8",
    "a9",
    "aa",
    "ab",
    "c601",
    "c602",
    "c610",
    "c611",
    "c621",
    "c622",
    "c631",
    "c632",
    "c641",
    "c642",
    "c902",
    "c904",
    "ca06",
    "ca03",
    "ca02",
    "ca22",
    "e505",
    "e703",
    "e707",
    "e750",
    "e751",
    "e752",
    "e753",
    "e761",
    "e762",
    "f203",
    "f204",
}


PARTIALLY_CONTROLLED_SEMANTICS = {
    "8003",
    "c2",
    "c7",
    "c660",
    "c666",
    "ca01",
    "ca41",
}


DISPUTED_SEMANTICS = {
    "8008",
    "a000",
    "c3",
    "c4",
    "c615",
    "c616",
    "d800",
    "d812",
    "e704",
    "e708",
    "f0",
    "f205",
}


SEMANTIC_NOTES = {
    "8003": (
        "Controlled positive and negative LightBurn Z-offset fixtures emit "
        "paired inverse signed millimetre deltas around job geometry. The "
        "physical axis effect has not been validated on hardware."
    ),
    "8008": "Axis identity differs across prior implementations.",
    "a000": "Axis identity differs across prior implementations.",
    "a9": (
        "Controlled diagonal-raster fixtures use signed relative X/Y cut "
        "motion to emit non-cardinal marked segments."
    ),
    "c3": "Tentative and encoder tables disagree on the laser index.",
    "c4": "Tentative and encoder tables disagree on the laser index.",
    "c2": (
        "Controlled grayscale fixtures emit this immediately after C7 "
        "with the same normalized position within the layer power range. "
        "Prior implementations call it laser 3, but the physical channel "
        "identity is unverified."
    ),
    "c7": (
        "Controlled grayscale fixtures emit this immediately before C2 "
        "with a normalized position within the layer power range. Changing "
        "only layer minimum power leaves this value unchanged."
    ),
    "c601": (
        "Controlled dual-head fixtures independently vary the laser 1 "
        "normal minimum-power command while laser 2 power remains fixed."
    ),
    "c602": (
        "Controlled dual-head fixtures independently vary the laser 1 "
        "normal maximum-power command while laser 2 power remains fixed."
    ),
    "c610": (
        "Controlled stationary-pulse fixtures interleave this command with "
        "non-cut moves. Its five-group payload is pulse duration in "
        "milliseconds scaled by 1000."
    ),
    "c611": (
        "Controlled start- and end-delay fixtures place this non-marking "
        "dwell before or after cut motion. Its five-group payload is dwell "
        "duration in milliseconds scaled by 1000."
    ),
    "c621": (
        "Controlled dual-head fixtures independently vary the laser 2 "
        "normal minimum-power command while laser 1 power remains fixed."
    ),
    "c622": (
        "Controlled dual-head fixtures independently vary the laser 2 "
        "normal maximum-power command while laser 1 power remains fixed."
    ),
    "c631": (
        "Controlled dual-head fixtures independently vary the layer's "
        "laser 1 normal minimum power while laser 2 power remains fixed."
    ),
    "c632": (
        "Controlled dual-head fixtures independently vary the layer's "
        "laser 1 normal maximum power while laser 2 power remains fixed."
    ),
    "c641": (
        "Controlled dual-head fixtures independently vary the layer's "
        "laser 2 normal minimum power while laser 1 power remains fixed."
    ),
    "c642": (
        "Controlled dual-head fixtures independently vary the layer's "
        "laser 2 normal maximum power while laser 1 power remains fixed."
    ),
    "c660": (
        "Controlled RF-tube profile fixtures emit two layer-adjacent "
        "records whose U35 payload follows the selected frequency in hertz. "
        "The legacy laser and layer field names are retained for catalog-v1 "
        "compatibility, but their physical selector roles are not "
        "independently established."
    ),
    "c666": (
        "Controlled fiber-profile fixtures vary the U35 payload with the "
        "selected pulse width in nanoseconds. Both selector bytes remain "
        "cautiously named because their roles are not independently "
        "established."
    ),
    "c615": "Timing meaning differs across prior implementations.",
    "c616": "Timing meaning differs across prior implementations.",
    "da00": (
        "Two pinned implementations report a controller-memory read "
        "followed by reply data. Hardware evidence is scoped to the "
        "request-context command."
    ),
    "da01": (
        "Two pinned implementations distinguish this controller-memory "
        "write from DA00. The executed mixed-job fixture contains the "
        "address-800 form, but no controlled hardware observation isolates "
        "its write effect."
    ),
    "ca01": (
        "Controlled leading operations select vector 0, horizontal "
        "bidirectional 1, horizontal unidirectional 2, vertical "
        "bidirectional 3, and vertical unidirectional 4. Air operations "
        "0x12 and 0x13 were also varied. Operation 5 occurs at contextual "
        "section boundaries, but is not evidence of diagonal or cross-hatch "
        "semantics. Operations 0x10 and 0x30 remain unnamed."
    ),
    "ca03": (
        "Controlled dual-head fixtures establish an enable bitmask: bit 0 "
        "enables head 1 and bit 1 enables head 2, producing values 1, 2, "
        "and 3 for head 1, head 2, and both heads."
    ),
    "ca41": (
        "For controlled LightBurn 2.1.03 Ruida profiles, values 1 through "
        "4 select horizontal unidirectional, horizontal bidirectional, "
        "vertical unidirectional, and vertical bidirectional processing. "
        "Value 0 covers general path-emitted processing, including vector "
        "work and diagonal raster paths represented as cut motion, so it "
        "does not by itself identify vector intent."
    ),
    "e704": "Field interpretation differs across prior implementations.",
    "e708": "Field interpretation differs across prior implementations.",
    "f205": "Field interpretation differs across prior implementations.",
}


DISPUTED_SEMANTIC_SOURCES = {
    "8008": (SRC_RUIDA_PA, SRC_MEERK40T),
    "a000": (SRC_RUIDA_PA, SRC_MEERK40T),
    "c3": (SRC_RUIDA_PA, SRC_MEERK40T),
    "c4": (SRC_RUIDA_PA, SRC_MEERK40T),
    "c615": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "c616": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "d800": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "d812": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "e704": (SRC_RUIDA_PA, SRC_MEERK40T),
    "e708": (SRC_RUIDA_PA, SRC_MEERK40T),
    "f0": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "f205": (SRC_RUIDA_PA, SRC_MEERK40T),
}


REPORTED_SHAPE_SOURCES = {
    "c615": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "c616": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "d812": (SRC_RUIDA_PA, SRC_MEERK40T, SRC_RUIDA_LASER),
    "da00": (SRC_RUIDA_PA, SRC_MEERK40T),
    "da01": (SRC_RUIDA_PA, SRC_MEERK40T),
}


REPORTED_SEMANTIC_SOURCES = {
    "da00": (SRC_RUIDA_PA, SRC_MEERK40T),
    "da01": (SRC_RUIDA_PA, SRC_MEERK40T),
}


CAPABILITY_SHAPE_OBSERVED = {
    "8003",
    "a9",
    "c610",
    "c611",
    "c660",
    "c666",
    "c903",
    "ca10",
}


CAPABILITY_SEMANTICS = {
    "8003",
    "a9",
    "c601",
    "c602",
    "c610",
    "c611",
    "c621",
    "c622",
    "c631",
    "c632",
    "c641",
    "c642",
    "c660",
    "c666",
    "ca01",
    "ca03",
    "ca41",
}


CONTROLLER_INTERACTIONS = {
    "da00": {
        "controller_effect": "read-only",
        "reply_behavior": "data",
        "reply_commands": ("setting_reply",),
        "reply_field_matches": (("address", "address"),),
    },
    "da01": {
        "controller_effect": "state-changing",
        "reply_behavior": "none",
    },
}


def _with_evidence(spec: CommandSpec) -> CommandSpec:
    opcode = spec.opcode.hex()
    interaction = CONTROLLER_INTERACTIONS.get(opcode, {})
    spec = replace(spec, **interaction)
    if opcode in LIGHTBURN_OBSERVED:
        semantic_evidence = "uncited-hypothesis"
        if opcode in CONTROLLED_SEMANTICS:
            semantic_evidence = "controlled-fixture"
        elif opcode in PARTIALLY_CONTROLLED_SEMANTICS:
            semantic_evidence = "partially-controlled"
        elif opcode in DISPUTED_SEMANTICS:
            semantic_evidence = "disputed"
        elif opcode in REPORTED_SEMANTIC_SOURCES:
            semantic_evidence = "reported"
        return replace(
            spec,
            shape_evidence="fixture-observed",
            semantic_evidence=semantic_evidence,
            shape_sources=(
                (SRC_LIGHTBURN_CAPABILITIES,)
                if opcode in CAPABILITY_SHAPE_OBSERVED
                else (SRC_LIGHTBURN,)
            ),
            semantic_sources=(
                (
                    (SRC_LIGHTBURN_CAPABILITIES,)
                    if opcode in CAPABILITY_SEMANTICS
                    else (SRC_LIGHTBURN,)
                )
                if semantic_evidence
                in ("controlled-fixture", "partially-controlled")
                else DISPUTED_SEMANTIC_SOURCES.get(
                    opcode,
                    REPORTED_SEMANTIC_SOURCES.get(opcode, ()),
                )
            ),
            notes=SEMANTIC_NOTES.get(opcode, spec.notes),
        )
    if opcode in PROVISIONAL:
        return replace(
            spec,
            shape_evidence="conflicting-reports",
            semantic_evidence="disputed",
            shape_sources=(SRC_RUIDA_PA, SRC_MEERK40T),
            semantic_sources=(SRC_RUIDA_PA, SRC_MEERK40T),
            notes=PROVISIONAL_NOTES[opcode],
        )
    return replace(
        spec,
        shape_evidence=(
            "reported"
            if opcode in REPORTED_SHAPE_SOURCES
            else "uncited-hypothesis"
        ),
        semantic_evidence=(
            "disputed"
            if opcode in DISPUTED_SEMANTICS
            else (
                "reported"
                if opcode in REPORTED_SEMANTIC_SOURCES
                else "uncited-hypothesis"
            )
        ),
        semantic_sources=DISPUTED_SEMANTIC_SOURCES.get(
            opcode,
            REPORTED_SEMANTIC_SOURCES.get(opcode, ()),
        ),
        shape_sources=REPORTED_SHAPE_SOURCES.get(opcode, ()),
        notes=SEMANTIC_NOTES.get(opcode, spec.notes),
    )


JOB_SPECS = tuple(_with_evidence(spec) for spec in SPECS)
DEFAULT_REGISTRY = CommandRegistry(JOB_SPECS)


PROVISIONAL_REQUEST_NAMES = {
    spec.name
    for spec in JOB_SPECS
    if spec.opcode[0] in (0xA5, 0xA7, 0xD8, 0xD9, 0xDA, 0xE5, 0xE8)
}


def _with_request_evidence(spec: CommandSpec) -> CommandSpec:
    if spec.name == "get_setting":
        return replace(
            spec,
            shape_evidence="hardware-observed",
            semantic_evidence="hardware-observed",
            shape_sources=HARDWARE_SETTING_SHAPE_SOURCES,
            semantic_sources=HARDWARE_SETTING_SOURCES,
            notes=HARDWARE_GET_SETTING_NOTES,
        )
    if spec.name == "process_stop":
        return replace(
            spec,
            shape_evidence="reported",
            semantic_evidence="reported",
            shape_sources=(SRC_MEERK40T,),
            semantic_sources=(SRC_MEERK40T,),
            notes=REPORTED_PROCESS_STOP_NOTES,
            controller_effect="machine-action",
            reply_behavior="none",
        )
    return spec


REQUEST_SPECS = tuple(
    _with_request_evidence(spec)
    for spec in JOB_SPECS
    if spec.name in PROVISIONAL_REQUEST_NAMES
) + (
    replace(
        _spec("ce", "keep_alive_request"),
        shape_evidence="reported",
        semantic_evidence="reported",
        shape_sources=(SRC_MEERK40T,),
        semantic_sources=(SRC_MEERK40T,),
        notes="Reported as an outbound controller keepalive.",
        controller_effect="read-only",
        reply_behavior="control",
    ),
)
REQUEST_REGISTRY = CommandRegistry(REQUEST_SPECS)


REPLY_SPECS = (
    replace(
        _spec("cc", "acknowledge"),
        shape_sources=(SRC_RUIDA_PA, SRC_MEERK40T),
        semantic_sources=(SRC_RUIDA_PA, SRC_MEERK40T),
    ),
    replace(
        _spec("cd", "error"),
        shape_sources=(SRC_RUIDA_PA, SRC_RUIDA_LASER),
        semantic_sources=(SRC_RUIDA_PA, SRC_RUIDA_LASER),
    ),
    replace(
        _spec("ce", "keep_alive"),
        shape_sources=(SRC_MEERK40T,),
        semantic_sources=(SRC_MEERK40T,),
    ),
    replace(
        _spec("cf", "negative_acknowledge"),
        shape_sources=(SRC_MEERK40T,),
        semantic_sources=(SRC_MEERK40T,),
        notes="Checksum rejection differs from the CD report.",
    ),
    replace(
        _spec(
            "da01",
            "setting_reply",
            U14Field("address"),
            U35Field("value"),
        ),
        shape_evidence="hardware-observed",
        semantic_evidence="hardware-observed",
        shape_sources=HARDWARE_SETTING_SHAPE_SOURCES,
        semantic_sources=HARDWARE_SETTING_SOURCES,
        notes=HARDWARE_SETTING_REPLY_NOTES,
    ),
    replace(
        _spec(
            "da01057f",
            "mainboard_version_reply_hypothesis",
            CStringField("version"),
        ),
        shape_evidence="conflicting-reports",
        semantic_evidence="disputed",
        shape_sources=(SRC_MEERK40T, SRC_RUIDA_PA),
        semantic_sources=(SRC_MEERK40T, SRC_RUIDA_PA),
        notes=(
            "CString exists only in a simulator; another report expects "
            "a fixed numeric reply. Hardware capture absent."
        ),
    ),
    replace(
        _spec(
            "da02",
            "setting_pair_reply",
            U14Field("address"),
            U35Field("first_value"),
            U35Field("second_value"),
        ),
        shape_sources=(SRC_RUIDA_LASER,),
        semantic_sources=(SRC_RUIDA_LASER,),
    ),
    replace(
        _spec("da05", "indexed_data_reply", BytesField("data", 20)),
        shape_evidence="simulator-only",
        semantic_evidence="unverified",
        shape_sources=(SRC_MEERK40T,),
        semantic_sources=(SRC_MEERK40T,),
        notes="Simulator behavior; hardware capture absent.",
    ),
    replace(
        _spec("da30", "card_data_reply", BytesField("data", 20)),
        shape_evidence="simulator-only",
        semantic_evidence="unverified",
        shape_sources=(SRC_MEERK40T,),
        semantic_sources=(SRC_MEERK40T,),
        notes="Explicitly unverified simulator response.",
    ),
    replace(
        _spec("da31", "card_data_reply_alt", BytesField("data", 20)),
        shape_evidence="simulator-only",
        semantic_evidence="unverified",
        shape_sources=(SRC_MEERK40T,),
        semantic_sources=(SRC_MEERK40T,),
        notes="Explicitly unverified simulator response.",
    ),
)


REPLY_REGISTRY = CommandRegistry(REPLY_SPECS)


REGISTRIES = {
    "job": DEFAULT_REGISTRY,
    "request": REQUEST_REGISTRY,
    "reply": REPLY_REGISTRY,
}
REGISTRY_CONTEXT_EVIDENCE = {
    "job": "mixed-catalog",
    "request": "provisional-family-selection",
    "reply": "mixed-catalog",
}


def get_registry(context: str) -> CommandRegistry:
    try:
        return REGISTRIES[context]
    except KeyError as error:
        raise ValueError(f"Unknown protocol context: {context}") from error
