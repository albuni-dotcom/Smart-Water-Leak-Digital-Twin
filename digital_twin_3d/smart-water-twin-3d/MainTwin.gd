extends Node3D
##
## Smart Water Leak Detection Digital Twin (V2) — Godot 4.3+
##
## Procedurally builds the 3D platform at runtime (no external .glb/.tscn
## required), listens on UDP for telemetry from mqtt_bridge.py, animates
## water flow through the pipes via a custom shader, colors each valve
## based on V (1=open=green, 0=closed=red), spawns leak particles on the
## affected branch when alarm_type says so, and exposes valve / reset
## buttons to the operator.
##
## Coordinate system (meters, +Y up):
##   base plate        Y = 0.000 .. 0.018
##   frame columns     Y = 0.030 .. 1.530
##   top tank bottom   Y = 1.530
##   manifold          Y = 0.980
##   main press. tap   Y = 1.160   (0.18 above manifold)
##   branch press. tap Y = 0.720   (0.26 below manifold, Δ=0.44 m)
##
## UDP contract (see mqtt_bridge.py):
##   bind  127.0.0.1:4243
##   send  {"__hello": true}                 -> 127.0.0.1:4242
##   recv  {"topic":"telemetry","ts":...,"data":{...}}
##   recv  {"topic":"status","ts":...,"data":"READY"|"ALARM"|...}
##   send  {"A":1} / {"ALL":0} / {"RESET":1} -> 127.0.0.1:4242
##

# ============================================================
# Networking
# ============================================================
const UDP_BIND_PORT := 4243
const BRIDGE_IP := "127.0.0.1"
const BRIDGE_PORT := 4242
const HELLO_INTERVAL := 2.0

# ============================================================
# Physical constants (must match firmware / BOM / SCAD)
# ============================================================
const TANK_VOLUME_L := 15.0
const TANK_HEIGHT_M := 0.20
const ZONES := ["A", "B", "C"]
const ZONE_X := [-0.115, 0.0, 0.115]

# ============================================================
# Shader — water flow inside pipes
# ============================================================
const WATER_SHADER := """
shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_disabled;

uniform float flow_speed : hint_range(0.0, 8.0) = 0.0;
uniform vec4 water_color : source_color = vec4(0.15, 0.55, 0.95, 0.55);
uniform vec4 band_color : source_color = vec4(0.80, 0.94, 1.0, 0.95);
uniform float band_density = 6.0;

varying float local_y;

void vertex() {
    local_y = VERTEX.y;
}

void fragment() {
    float speed_scale = smoothstep(0.0, 0.5, flow_speed);
    if (speed_scale < 0.01) {
        ALBEDO = water_color.rgb;
        ALPHA = water_color.a * 0.65;
        EMISSION = water_color.rgb * 0.08;
        ROUGHNESS = 0.35;
        METALLIC = 0.05;
        return;
    }
    float coord = (local_y + TIME * flow_speed * 0.2) * band_density;
    float bands = fract(coord);
    float mask = smoothstep(0.15, 0.35, bands) * (1.0 - smoothstep(0.65, 0.85, bands));
    vec4 col = mix(water_color, band_color, mask * speed_scale);
    ALBEDO = col.rgb;
    ALPHA = mix(water_color.a, 0.95, mask * speed_scale);
    EMISSION = water_color.rgb * (0.12 + 0.35 * mask * speed_scale);
    ROUGHNESS = 0.1;
    METALLIC = 0.1;
}
"""

# ============================================================
# Networking state
# ============================================================
var _udp: PacketPeerUDP
var _last_hello_ts := -1.0

# ============================================================
# Telemetry state
# ============================================================
var _telemetry: Dictionary = {}
var _status_text: String = "DISCONNECTED"
var _tank_volume_l: float = 14.5

# ============================================================
# References to scene nodes built in _ready()
# ============================================================
var _pipe_main_mat: ShaderMaterial
var _pipe_zone_mats: Array[ShaderMaterial] = []
var _valve_mats: Array[StandardMaterial3D] = []
var _leak_emitters: Array[GPUParticles3D] = []
var _main_leak_emitter: GPUParticles3D
var _tank_water_node: MeshInstance3D
var _tank_water_bottom_y: float

# ============================================================
# Camera (orbit)
# ============================================================
var _camera: Camera3D
var _cam_target := Vector3(0.0, 0.75, 0.0)
var _cam_distance := 3.6
var _cam_distance_target := 3.6
var _cam_yaw := 0.7
var _cam_pitch := 0.45
var _cam_min_distance := 1.5
var _cam_max_distance := 8.0
var _cam_min_pitch := -0.2
var _cam_max_pitch := 1.4
var _dragging := false

# ============================================================
# UI state
# ============================================================
var _status_label: Label
var _alarm_label: Label

# ============================================================
# Lifecycle
# ============================================================
func _ready() -> void:
	_build_environment()
	_build_frame()
	_build_tanks()
	_build_pipes_and_valves()
	_build_leak_particles()
	_build_camera()
	_build_ui()
	_setup_udp()
	_send_hello()


func _process(delta: float) -> void:
	_poll_udp()
	_update_camera(delta)
	_update_tank_level(delta)
	_update_ui()

	if Time.get_ticks_msec() / 1000.0 - _last_hello_ts > HELLO_INTERVAL:
		_send_hello()


func _exit_tree() -> void:
	if _udp != null:
		_udp.close()


# ============================================================
# Networking
# ============================================================
func _setup_udp() -> void:
	_udp = PacketPeerUDP.new()
	var err := _udp.bind(UDP_BIND_PORT, "127.0.0.1")
	if err != OK:
		push_error(
			"MainTwin: cannot bind UDP %d (error %d). Another instance " %
			[UDP_BIND_PORT, err] + "may be running."
		)


func _send_hello() -> void:
	if _udp == null:
		return
	var payload := JSON.stringify({"__hello": true})
	_udp.set_dest_address(BRIDGE_IP, BRIDGE_PORT)
	_udp.put_packet(payload.to_utf8_buffer())
	_last_hello_ts = Time.get_ticks_msec() / 1000.0


func _send_command(cmd: Dictionary) -> void:
	if _udp == null:
		return
	var payload := JSON.stringify(cmd)
	_udp.set_dest_address(BRIDGE_IP, BRIDGE_PORT)
	_udp.put_packet(payload.to_utf8_buffer())


func _poll_udp() -> void:
	if _udp == null:
		return
	while _udp.get_available_packet_count() > 0:
		var bytes := _udp.get_packet()
		var text := bytes.get_string_from_utf8()
		var parsed = JSON.parse_string(text)
		if not (parsed is Dictionary):
			continue
		var envelope: Dictionary = parsed
		var topic := str(envelope.get("topic", ""))
		var data = envelope.get("data")
		if topic == "telemetry" and data is Dictionary:
			_telemetry = data
			_apply_telemetry(_telemetry)
		elif topic == "status":
			_status_text = str(data)
			_update_ui()


# ============================================================
# Telemetry application
# ============================================================
func _apply_telemetry(data: Dictionary) -> void:
	# --- Flow → shader speed ---
	var q_main := float(data.get("Q_main", 0.0))
	if _pipe_main_mat != null:
		_pipe_main_mat.set_shader_parameter("flow_speed", clampf(q_main, 0.0, 4.0))

	for i in ZONES.size():
		var zone_data = data.get("Z" + ZONES[i], {})
		var q_zone := 0.0
		if zone_data is Dictionary:
			q_zone = float(zone_data.get("Q", 0.0))
		if i < _pipe_zone_mats.size():
			_pipe_zone_mats[i].set_shader_parameter(
				"flow_speed", clampf(q_zone, 0.0, 4.0)
			)

	# --- Valve colors ---
	for i in ZONES.size():
		var zone_data = data.get("Z" + ZONES[i], {})
		var v_open := false
		if zone_data is Dictionary:
			v_open = int(zone_data.get("V", 0)) == 1
		if i < _valve_mats.size():
			_valve_mats[i].albedo_color = (
				Color(0.15, 0.85, 0.25) if v_open else Color(0.85, 0.15, 0.15)
			)
			_valve_mats[i].emission = (
				Color(0.05, 0.35, 0.1) if v_open else Color(0.35, 0.05, 0.05)
			)

	# --- Leak particles ---
	var alarm_type := str(data.get("alarm_type", "NONE"))
	for i in ZONES.size():
		if i < _leak_emitters.size():
			_leak_emitters[i].emitting = (
				alarm_type == "ZONE_" + ZONES[i] + "_LEAK"
			)
	if _main_leak_emitter != null:
		_main_leak_emitter.emitting = (alarm_type == "MAIN_MANIFOLD_LEAK")

	_update_ui()


func _update_tank_level(delta: float) -> void:
	if _tank_water_node == null:
		return

	var q_main := 0.0
	if _telemetry.has("Q_main"):
		q_main = float(_telemetry.get("Q_main", 0.0))

	_tank_volume_l = clampf(
		_tank_volume_l - (q_main / 60.0) * delta, 0.0, TANK_VOLUME_L
	)

	var level := _tank_volume_l / TANK_VOLUME_L
	level = clampf(level, 0.02, 1.0)

	_tank_water_node.scale.y = level
	_tank_water_node.position.y = _tank_water_bottom_y + (TANK_HEIGHT_M * level) / 2.0


# ============================================================
# Camera (orbit + zoom)
# ============================================================
func _build_camera() -> void:
	_camera = Camera3D.new()
	_camera.fov = 55.0
	_camera.near = 0.05
	_camera.far = 100.0
	add_child(_camera)
	_apply_camera_transform()


func _apply_camera_transform() -> void:
	var cos_p := cos(_cam_pitch)
	var offset := Vector3(
		cos(_cam_yaw) * cos_p * _cam_distance,
		sin(_cam_pitch) * _cam_distance,
		sin(_cam_yaw) * cos_p * _cam_distance
	)
	_camera.position = _cam_target + offset
	_camera.look_at(_cam_target, Vector3.UP)


func _update_camera(delta: float) -> void:
	if _cam_distance != _cam_distance_target:
		_cam_distance = lerpf(_cam_distance, _cam_distance_target, delta * 10.0)
	_apply_camera_transform()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.button_index == MOUSE_BUTTON_LEFT:
			_dragging = mb.pressed
		elif mb.pressed and mb.button_index == MOUSE_BUTTON_WHEEL_UP:
			_cam_distance_target = clampf(
				_cam_distance_target * 0.9, _cam_min_distance, _cam_max_distance
			)
		elif mb.pressed and mb.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_cam_distance_target = clampf(
				_cam_distance_target * 1.1, _cam_min_distance, _cam_max_distance
			)
	elif event is InputEventMouseMotion and _dragging:
		var mm := event as InputEventMouseMotion
		_cam_yaw -= mm.relative.x * 0.01
		_cam_pitch = clampf(
			_cam_pitch + mm.relative.y * 0.01, _cam_min_pitch, _cam_max_pitch
		)


# ============================================================
# Scene construction — environment
# ============================================================
func _build_environment() -> void:
	# Directional light
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-50, -35, 0)
	light.light_energy = 1.3
	light.shadow_enabled = true
	add_child(light)

	# Soft ambient + sky
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = Color(0.20, 0.35, 0.55)
	sky_mat.sky_horizon_color = Color(0.55, 0.65, 0.75)
	sky_mat.ground_bottom_color = Color(0.20, 0.22, 0.25)
	sky_mat.ground_horizon_color = Color(0.40, 0.45, 0.50)
	sky.sky_material = sky_mat
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = 0.6

	var world_env := WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)

	# Ground plane
	var ground := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(12, 12)
	ground.mesh = plane
	ground.position = Vector3(0, -0.002, 0)
	var gmat := StandardMaterial3D.new()
	gmat.albedo_color = Color(0.35, 0.36, 0.32)
	gmat.roughness = 1.0
	ground.material_override = gmat
	add_child(ground)


# ============================================================
# Scene construction — frame and base
# ============================================================
func _make_box(size: Vector3, pos: Vector3, color: Color,
		parent: Node3D = self, metallic := 0.0,
		emission := Color(0, 0, 0)) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = size
	var inst := MeshInstance3D.new()
	inst.mesh = mesh
	inst.position = pos
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.metallic = metallic
	mat.roughness = 0.5
	mat.emission_enabled = emission != Color(0, 0, 0)
	if mat.emission_enabled:
		mat.emission = emission
	inst.material_override = mat
	parent.add_child(inst)
	return inst


func _make_cylinder(radius: float, height: float, pos: Vector3,
		mat: Material, parent: Node3D = self,
		rotate_x_deg := 0.0) -> MeshInstance3D:
	var mesh := CylinderMesh.new()
	mesh.top_radius = radius
	mesh.bottom_radius = radius
	mesh.height = height
	mesh.radial_segments = 16
	mesh.rings = 1
	var inst := MeshInstance3D.new()
	inst.mesh = mesh
	inst.position = pos
	if rotate_x_deg != 0.0:
		inst.rotation_degrees = Vector3(rotate_x_deg, 0, 0)
	inst.material_override = mat
	parent.add_child(inst)
	return inst


func _build_frame() -> void:
	# Base plate
	_make_box(
		Vector3(0.62, 0.018, 0.42), Vector3(0, 0.009, 0),
		Color(0.42, 0.30, 0.20)
	)

	# 4 aluminum columns (20x20 mm)
	var silver := Color(0.78, 0.80, 0.82)
	for x in [-0.175, 0.175]:
		for z in [-0.145, 0.145]:
			_make_box(
				Vector3(0.020, 1.500, 0.020),
				Vector3(x, 0.030 + 0.750, z),
				silver, self, 0.6
			)

	# Top square frame
	for z in [-0.145, 0.145]:
		_make_box(
			Vector3(0.370, 0.020, 0.020),
			Vector3(0, 1.520, z), silver, self, 0.6
		)
	for x in [-0.175, 0.175]:
		_make_box(
			Vector3(0.020, 0.020, 0.310),
			Vector3(x, 1.520, 0), silver, self, 0.6
		)

	# Two horizontal support bars at Y = 0.55 and Y = 0.85 (matches SCAD)
	for y in [0.550, 0.850]:
		_make_box(
			Vector3(0.370, 0.020, 0.020),
			Vector3(0, y, -0.145), silver, self, 0.6
		)


# ============================================================
# Scene construction — tanks
# ============================================================
func _build_tanks() -> void:
	# --- Top tank (15 L, transparent) ---
	var top_tank_bottom := 1.530
	var tank_size := Vector3(0.30, TANK_HEIGHT_M, 0.25)

	# Outer shell
	var tank_inst := _make_box(
		tank_size,
		Vector3(0, top_tank_bottom + TANK_HEIGHT_M / 2.0, 0),
		Color(0.80, 0.90, 1.0, 0.20)
	)
	var tank_mat := tank_inst.material_override as StandardMaterial3D
	tank_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	tank_mat.albedo_color = Color(0.80, 0.90, 1.0, 0.20)
	tank_mat.metallic = 0.1
	tank_mat.roughness = 0.05

	# Water column (child mesh, scaled in _update_tank_level)
	var water_mesh := BoxMesh.new()
	water_mesh.size = Vector3(0.28, TANK_HEIGHT_M, 0.23)
	_tank_water_node = MeshInstance3D.new()
	_tank_water_node.mesh = water_mesh
	_tank_water_bottom_y = top_tank_bottom + 0.005
	_tank_water_node.position = Vector3(
		0, _tank_water_bottom_y + TANK_HEIGHT_M / 2.0, 0
	)
	var wmat := StandardMaterial3D.new()
	wmat.albedo_color = Color(0.10, 0.55, 0.95, 0.65)
	wmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	wmat.emission_enabled = true
	wmat.emission = Color(0.05, 0.20, 0.40)
	wmat.roughness = 0.05
	_tank_water_node.material_override = wmat
	add_child(_tank_water_node)

	# --- Bottom collection tank (18 L, transparent) ---
	var bot_size := Vector3(0.40, 0.15, 0.30)
	var bot_inst := _make_box(
		bot_size, Vector3(0, 0.030 + 0.075, 0),
		Color(0.80, 0.90, 1.0, 0.18)
	)
	var bmat := bot_inst.material_override as StandardMaterial3D
	bmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	bmat.roughness = 0.05


# ============================================================
# Scene construction — pipes and valves
# ============================================================
func _make_water_material() -> ShaderMaterial:
	var shader := Shader.new()
	shader.code = WATER_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = shader
	mat.set_shader_parameter("flow_speed", 0.0)
	return mat


func _build_pipes_and_valves() -> void:
	const MANIFOLD_Y := 0.980
	const MAIN_TOP_Y := 1.530
	const BRANCH_BOTTOM_Y := 0.160

	# --- Main line (12 mm diameter, radius 6 mm) ---
	_pipe_main_mat = _make_water_material()
	_make_cylinder(
		0.006, MAIN_TOP_Y - MANIFOLD_Y,
		Vector3(0, MANIFOLD_Y + (MAIN_TOP_Y - MANIFOLD_Y) / 2.0, -0.100),
		_pipe_main_mat
	)

	# --- Distribution manifold (horizontal cylinder along X) ---
	var manifold_mat := _make_water_material()
	manifold_mat.set_shader_parameter("flow_speed", 0.0)
	_make_cylinder(
		0.013, 0.270,
		Vector3(0, MANIFOLD_Y, -0.100),
		manifold_mat,
		self,
		90.0
	)

	# --- Branch pipes (8 mm diameter, radius 4 mm) + valves ---
	for i in ZONES.size():
		var x: float = ZONE_X[i]

		var branch_mat := _make_water_material()
		_pipe_zone_mats.append(branch_mat)
		_make_cylinder(
			0.004, MANIFOLD_Y - BRANCH_BOTTOM_Y,
			Vector3(x, BRANCH_BOTTOM_Y + (MANIFOLD_Y - BRANCH_BOTTOM_Y) / 2.0, -0.100),
			branch_mat
		)

		# Valve body at Y = 0.91
		var valve_color := Color(0.85, 0.15, 0.15)
		var valve := _make_box(
			Vector3(0.030, 0.030, 0.030),
			Vector3(x, 0.910, -0.100),
			valve_color,
			self,
			0.4,
			Color(0.35, 0.05, 0.05)
		)
		_valve_mats.append(valve.material_override as StandardMaterial3D)

		# Zone label above the valve
		var label := Label3D.new()
		label.text = ZONES[i]
		label.position = Vector3(x, 0.955, -0.100)
		label.font_size = 20
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.modulate = Color.WHITE
		label.outline_size = 8
		add_child(label)


# ============================================================
# Scene construction — leak particles
# ============================================================
func _make_leak_emitter(pos: Vector3, direction: Vector3,
		spread: float, velocity: float) -> GPUParticles3D:
	var p := GPUParticles3D.new()
	p.position = pos
	p.amount = 220
	p.lifetime = 1.6
	p.emitting = false
	p.local_coords = false

	var pm := ParticleProcessMaterial.new()
	pm.direction = direction.normalized()
	pm.spread = spread
	pm.initial_velocity_min = velocity * 0.6
	pm.initial_velocity_max = velocity * 1.4
	pm.gravity = Vector3(0, -9.8, 0)
	pm.scale_min = 0.5
	pm.scale_max = 1.2
	pm.color = Color(0.35, 0.65, 1.0, 0.85)
	p.process_material = pm

	var quad := QuadMesh.new()
	quad.size = Vector2(0.018, 0.018)
	var qmat := StandardMaterial3D.new()
	qmat.albedo_color = Color(0.45, 0.75, 1.0, 0.85)
	qmat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	qmat.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	qmat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	quad.material = qmat
	p.draw_pass_1 = quad

	add_child(p)
	return p


func _build_leak_particles() -> void:
	const LEAK_POINT_Y := 0.640
	for i in ZONES.size():
		var x: float = ZONE_X[i]
		_leak_emitters.append(_make_leak_emitter(
			Vector3(x + 0.03, LEAK_POINT_Y, -0.100),
			Vector3(1.0, -0.4, 0.0),
			35.0,
			1.6
		))

	_main_leak_emitter = _make_leak_emitter(
		Vector3(0, 0.985, -0.100),
		Vector3(0, -1, 0),
		60.0,
		2.0
	)


# ============================================================
# UI
# ============================================================
func _build_ui() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)

	# --- Status panel (top-left) ---
	var status_panel := PanelContainer.new()
	status_panel.position = Vector2(16, 16)
	status_panel.custom_minimum_size = Vector2(280, 0)
	layer.add_child(status_panel)

	var status_vbox := VBoxContainer.new()
	status_vbox.add_theme_constant_override("separation", 4)
	status_panel.add_child(status_vbox)

	var title := Label.new()
	title.text = "Smart Water Leak Digital Twin (V2)"
	title.add_theme_font_size_override("font_size", 16)
	status_vbox.add_child(title)

	_status_label = Label.new()
	_status_label.text = "MQTT: DISCONNECTED"
	status_vbox.add_child(_status_label)

	_alarm_label = Label.new()
	_alarm_label.text = "Alarm: NONE"
	_alarm_label.add_theme_color_override("font_color", Color(0.85, 0.85, 0.85))
	status_vbox.add_child(_alarm_label)

	# --- Valve control panel (top-right) ---
	var controls := PanelContainer.new()
	controls.anchor_left = 1.0
	controls.anchor_right = 1.0
	controls.offset_left = -260
	controls.offset_right = -16
	controls.offset_top = 16
	layer.add_child(controls)

	var controls_vbox := VBoxContainer.new()
	controls_vbox.add_theme_constant_override("separation", 6)
	controls.add_child(controls_vbox)

	var ctrl_title := Label.new()
	ctrl_title.text = "Valve Controls"
	ctrl_title.add_theme_font_size_override("font_size", 14)
	controls_vbox.add_child(ctrl_title)

	for i in ZONES.size():
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 6)
		var zone_name: String = ZONES[i]
		var open_btn := Button.new()
		open_btn.text = "Open " + zone_name
		open_btn.custom_minimum_size = Vector2(110, 32)
		open_btn.pressed.connect(
			func() -> void: _send_command({zone_name: 1})
		)
		row.add_child(open_btn)

		var close_btn := Button.new()
		close_btn.text = "Close " + zone_name
		close_btn.custom_minimum_size = Vector2(110, 32)
		close_btn.pressed.connect(
			func() -> void: _send_command({zone_name: 0})
		)
		row.add_child(close_btn)

		controls_vbox.add_child(row)

	var sep := HSeparator.new()
	controls_vbox.add_child(sep)

	var all_row := HBoxContainer.new()
	all_row.add_theme_constant_override("separation", 6)

	var open_all := Button.new()
	open_all.text = "Open All"
	open_all.custom_minimum_size = Vector2(110, 32)
	open_all.pressed.connect(func() -> void: _send_command({"ALL": 1}))
	all_row.add_child(open_all)

	var close_all := Button.new()
	close_all.text = "Close All"
	close_all.custom_minimum_size = Vector2(110, 32)
	close_all.pressed.connect(func() -> void: _send_command({"ALL": 0}))
	all_row.add_child(close_all)

	controls_vbox.add_child(all_row)

	var reset_btn := Button.new()
	reset_btn.text = "RESET (keep valves closed)"
	reset_btn.custom_minimum_size = Vector2(230, 34)
	reset_btn.pressed.connect(func() -> void: _send_command({"RESET": 1}))
	controls_vbox.add_child(reset_btn)

	var hint := Label.new()
	hint.text = "Drag to orbit • Scroll to zoom"
	hint.add_theme_font_size_override("font_size", 11)
	hint.add_theme_color_override("font_color", Color(0.8, 0.8, 0.8))
	controls_vbox.add_child(hint)


func _update_ui() -> void:
	if _status_label == null or _alarm_label == null:
		return

	var status_display := _status_text
	var color := Color(0.9, 0.9, 0.9)
	match _status_text:
		"READY":
			color = Color(0.3, 0.9, 0.4)
		"ALARM":
			color = Color(0.95, 0.4, 0.4)
		"DISCONNECTED":
			color = Color(0.6, 0.6, 0.6)
	_status_label.text = "MQTT: " + status_display
	_status_label.add_theme_color_override("font_color", color)

	var alarm_type := "NONE"
	if _telemetry.has("alarm_type"):
		alarm_type = str(_telemetry.get("alarm_type", "NONE"))

	if alarm_type == "NONE":
		_alarm_label.text = "Alarm: NONE"
		_alarm_label.add_theme_color_override("font_color", Color(0.7, 0.9, 0.7))
	else:
		_alarm_label.text = "Alarm: " + alarm_type
		_alarm_label.add_theme_color_override("font_color", Color(1.0, 0.4, 0.4))

	var tank_pct := int(round(_tank_volume_l / TANK_VOLUME_L * 100.0))
	_alarm_label.tooltip_text = "Tank: %d%%" % tank_pct
