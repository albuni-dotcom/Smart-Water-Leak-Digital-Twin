// =========================================================================
// التصميم الميكانيكي ثلاثي الأبعاد المنقح (V2 - High-Detail 3D CAD)
// متطابق 100% مع الوثيقة الهندسية وأكواد المعالجة
// =========================================================================

$fn = 40;

// --- الأبعاد الهندسية المعتمدة (mm) ---
base_w = 620;
base_d = 420;
base_h = 18;

frame_h = 1500;
profile = 20;

top_tank_w = 300;
top_tank_d = 250;
top_tank_h = 200;

bot_tank_w = 400;
bot_tank_d = 300;
bot_tank_h = 150;

// =========================================================================
// وحدات المكونات التفصيلية
// =========================================================================

module detailed_solenoid_valve() {
    color("Goldenrod") {
        cylinder(h = 24, r = 13, center = true);
        rotate([0, 90, 0]) cylinder(h = 46, r = 8, center = true);
    }
    color("RoyalBlue")
    translate([0, 0, 22])
        cube([26, 26, 28], center = true);
    color("Black")
    translate([0, 0, 38])
        cube([16, 16, 6], center = true);
}

module detailed_flow_sensor(scale_factor = 1.0, body_color = "White") {
    scale([scale_factor, scale_factor, scale_factor]) {
        color(body_color) {
            rotate([90, 0, 0]) cylinder(h = 18, r = 16, center = true);
            cylinder(h = 54, r = 7, center = true);
        }
        color("DimGray")
        translate([0, -11, 0])
            cube([14, 6, 18], center = true);
        color("Red")
        translate([0, -11, 12])
            cylinder(h = 10, r = 1.5);
    }
}

module detailed_pressure_airtrap() {
    color("White") {
        cylinder(h = 26, r = 6, center = true);
        rotate([0, 90, 0]) cylinder(h = 18, r = 5);
    }
    color([0.9, 0.95, 1.0, 0.5])
    translate([16, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 45, r = 4);
    color("DodgerBlue")
    translate([63, 0, 0])
        cube([4, 18, 18], center = true);
    color("Silver")
    translate([60, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 6, r = 3);
}

module detailed_needle_valve() {
    color("Gold") {
        cylinder(h = 34, r = 8, center = true);
        rotate([0, 90, 0]) cylinder(h = 22, r = 6);
    }
    color("Silver")
    translate([22, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 15, r = 2.5);
    color("Crimson")
    translate([37, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 6, r = 14);
}

module pipe_clamp() {
    color("Black") {
        difference() {
            cylinder(h = 12, r = 8.5, center = true);
            cylinder(h = 14, r = 4.2, center = true);
        }
        // ذراع التثبيت الممتد ليلامس العارضة الأفقية عند y=-145
        translate([0, -22, 0]) cube([14, 38, 12], center = true);
    }
}

// =========================================================================
// التجميع الرئيسي للمجسم
// =========================================================================

module base_plate() {
    color("SaddleBrown")
    translate([-base_w/2, -base_d/2, 12])
        cube([base_w, base_d, base_h]);
    
    color("Black")
    for (x = [-base_w/2 + 30, base_w/2 - 30])
        for (y = [-base_d/2 + 30, base_d/2 - 30])
            translate([x, y, 0])
                cylinder(h = 12, r = 18);
}

module aluminum_frame() {
    z_start = base_h + 12;
    col_x = 175;
    col_y = 145;

    color("Silver") {
        // 4 أعمدة رأسية
        for (x = [-col_x, col_x])
            for (y = [-col_y, col_y])
                translate([x - profile/2, y - profile/2, z_start])
                    cube([profile, profile, frame_h]);

        // الإطار العلوي
        for (y = [-col_y, col_y])
            translate([-col_x - profile/2, y - profile/2, z_start + frame_h - profile])
                cube([col_x*2 + profile, profile, profile]);
        for (x = [-col_x, col_x])
            translate([x - profile/2, -col_y - profile/2, z_start + frame_h - profile])
                cube([profile, col_y*2 + profile, profile]);

        // إكمال العارضتين الأفقيتين لتثبيت المواسير والحساسات
        for (z_bar = []) {
            translate([-col_x - profile/2, -col_y - profile/2, z_start + z_bar])
                cube([col_x*2 + profile, profile, profile]);
        }
    }

    // زوايا ربط معدنية في الأركان
    color("DimGray")
    for (x = [-col_x + 15, col_x - 15])
        for (y = [-col_y, col_y])
            translate([x, y, z_start + 15])
                cube([18, 18, 18], center = true);

    // التدعيم القطري الجانبي
    color("DarkSlateGray")
    for (x = [-col_x, col_x]) {
        translate([x, 0, z_start + frame_h/2])
            rotate([atan2(frame_h, col_y*2), 0, 0])
                cube([6, sqrt(pow(frame_h,2) + pow(col_y*2,2)), 6], center = true);
    }
}

module top_tank_assembly() {
    z_pos = base_h + 12 + frame_h;
    
    color([0.85, 0.95, 1.0, 0.35])
    translate([-top_tank_w/2, -top_tank_d/2, z_pos])
        difference() {
            cube([top_tank_w, top_tank_d, top_tank_h]);
            translate([4, 4, 4])
                cube([top_tank_w - 8, top_tank_d - 8, top_tank_h]);
        }

    // عمود الماء (12 لتر قياسي = ارتفاع 170 مم)
    color([0.05, 0.55, 0.95, 0.55])
    translate([-top_tank_w/2 + 5, -top_tank_d/2 + 5, z_pos + 5])
        cube([top_tank_w - 10, top_tank_d - 10, 170]);

    // 3 حواجز داخلية مثقبة لمنع التموج
    color([1, 1, 1, 0.65])
    for (i = [-75, 0, 75]) {
        translate([i, -top_tank_d/2 + 6, z_pos + 25])
            cube([3, top_tank_d - 12, top_tank_h - 45]);
    }

    color("Black")
    translate([0, -100, z_pos - 8])
        cylinder(h = 16, r = 12);
}

module bottom_tank() {
    z_pos = base_h + 12;
    color([0.85, 0.95, 1.0, 0.4])
    translate([-bot_tank_w/2, -bot_tank_d/2, z_pos])
        difference() {
            cube([bot_tank_w, bot_tank_d, bot_tank_h]);
            translate([4, 4, 4])
                cube([bot_tank_w - 8, bot_tank_d - 8, bot_tank_h]);
        }
}

module hydraulic_and_electronics() {
    z_top = base_h + 12 + frame_h;
    z_manifold = base_h + 12 + 950;

    // الخط الرئيسي (قطر 12 مم -> نصف قطر 6 مم)
    color("DodgerBlue")
    translate([0, -100, z_manifold])
        cylinder(h = z_top - z_manifold, r = 6);

    color("DarkCyan")
    translate([0, -100, z_top - 120])
        cylinder(h = 65, r = 18, center = true);

    translate([0, -100, z_manifold + 320])
        detailed_flow_sensor(1.2, "Black");

    // حساس الضغط الرئيسي (z = 1160 mm)
    translate([0, -100, z_manifold + 180])
        detailed_pressure_airtrap();

    color("SteelBlue")
    translate([-135, -100, z_manifold])
        rotate([0, 90, 0]) cylinder(h = 270, r = 13);

    // الفروع الثلاثة (قطر 8 مم -> نصف قطر 4.0 مم مطابق للوثيقة)
    zone_x = [-115, 0, 115];
    for (i = [0 : 2]) {
        x = zone_x[i];

        color("DeepSkyBlue")
        translate([x, -100, base_h + 130])
            cylinder(h = z_manifold - (base_h + 130), r = 4.0);

        translate([x, -100, z_manifold - 70])
            rotate([0, 90, 0]) detailed_solenoid_valve();

        // مشبك تثبيت علوي متصل بالعارضة
        translate([x, -100, base_h + 12 + 860])
            pipe_clamp();

        translate([x, -100, z_manifold - 180])
            detailed_flow_sensor(0.85, "White");

        // حساس الضغط الفرعي (z = 720 mm -> فرق ارتفاع 0.44m عن الرئيسي)
        translate([x, -100, z_manifold - 260])
            detailed_pressure_airtrap();

        color("Crimson")
        translate([x - 14, -100, z_manifold - 340])
            rotate([0, 90, 0]) cylinder(h = 20, r = 4);

        // مشبك تثبيت سفلي متصل بالعارضة
        translate([x, -100, base_h + 12 + 560])
            pipe_clamp();

        translate([x, -100, z_manifold - 460])
            detailed_needle_valve();
    }

    // صندوق التحكم الإلكتروني مثبت مباشرة على العمود الأيمن (x = 175)
    box_x = 220;
    box_y = -110;
    box_z = z_manifold - 220;

    color("DimGray")
    translate([box_x, box_y, box_z])
        cube([70, 140, 220], center = true);

    color([0.9, 0.95, 1.0, 0.3])
    translate([box_x + 36, box_y, box_z])
        cube([3, 134, 214], center = true);

    color("Navy")
    translate([box_x + 25, box_y - 30, box_z + 55])
        cube([8, 28, 55], center = true);

    color("DeepSkyBlue")
    translate([box_x + 25, box_y + 25, box_z + 65])
        cube([6, 18, 28], center = true);

    color("DodgerBlue")
    translate([box_x + 22, box_y, box_z - 35])
        cube([18, 75, 55], center = true);
}

base_plate();
aluminum_frame();
top_tank_assembly();
bottom_tank();
hydraulic_and_electronics();
