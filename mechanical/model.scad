// =========================================================================
// التصميم الميكانيكي ثلاثي الأبعاد (V3 - High-Detail 3D CAD)
// مشروع كشف وعزل تسربات المياه الذكي - تخصص هندسة الميكاترونيات 2026
// =========================================================================
// التغييرات المعتمدة في هذه النسخة (V3):
//   1. إظهار العوارض الأفقية عند z = 550 و z = 850 ملم
//      (z_bar = [550, 850]) لتثبيت مشابك الأنابيب فعلياً.
//   2. نصف قطر أنابيب الفروع = 4.0 ملم (قطر 8 ملم) لمطابقة
//      الوثيقة الهندسية وحساسات YF-S401 (1/4").
//   3. نصف قطر خرطوم عمود الهواء (Air Trap) = 3.0 ملم.
//   4. محاذاة مواضع مشابك الأنابيب مع العوارض (z = 550 و z = 850).
//
// ملاحظة حول فرق الارتفاع 0.44 م:
//   حساس الضغط الرئيسي (على المشعب) عند z_manifold + 180
//   حساسات الضغط الفرعية عند z_manifold - 260
//   فرق الارتفاع الفعلي = 440 ملم = 0.44 م (مطابق للوثيقة والكود).
//
// ملاحظة حول شاشة OLED:
//   الشاشة موجودة داخل صندوق التحكم في هذا الرسم. إذا اعتمدت BOM
//   النهائية عدم شراء الشاشة (الاكتفاء بلوحة Streamlit)، احذف
//   الكتلة المشار إليها بـ "OLED: decision pending" أدناه.
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

// 1. صمام العزل الكهربائي (12V Direct Acting Solenoid Valve)
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

// 2. حساس التدفق التوربيني (YF-S201 / YF-S401)
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

// 3. حساس الضغط مع عمود الهواء المحبوس (XGZP6847A + Air Trap)
module detailed_pressure_airtrap() {
    // وصلة T على الأنبوب
    color("White") {
        cylinder(h = 26, r = 6, center = true);
        rotate([0, 90, 0]) cylinder(h = 18, r = 5);
    }
    // أنبوب الهواء الشفاف (نصف القطر 3.0 ملم)
    color([0.9, 0.95, 1.0, 0.5])
    translate([16, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 45, r = 3.0);
    // شريحة حساس الضغط XGZP6847A في النهاية
    color("DodgerBlue")
    translate([63, 0, 0])
        cube([4, 18, 18], center = true);
    color("Silver")
    translate([60, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 6, r = 3);
}

// 4. صمام الخنق الإبري النحاسي (Brass Needle Valve)
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

// 5. مشبك تثبيت المواسير (Pipe Clamp)
//    الحلقة الداخلية = 4.2 ملم (تلائم الأنبوب 4.0 ملم).
//    الذراع الأفقية تمتد في اتجاه -y حتى تلامس العارضة عند y = -135.
module pipe_clamp() {
    color("Black") {
        difference() {
            cylinder(h = 12, r = 8.5, center = true);
            cylinder(h = 14, r = 4.2, center = true);
        }
        // ذراع التثبيت الممتد ليلامس العارضة الأفقية
        translate([0, -22, 0]) cube([14, 38, 12], center = true);
    }
}

// =========================================================================
// التجميع الرئيسي للمجسم
// =========================================================================

// 1. القاعدة والأقدام المطاطية
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

// 2. هيكل الألومنيوم 2020 مع زوايا الربط والتدعيم القطري
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

        // العوارض الأفقية لتثبيت المواسير والحساسات
        // z = 550 (دعم أسفل الصمامات الإبرية) و z = 850 (دعم أعلى)
        for (z_bar = [550, 850]) {
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
                cube([6, sqrt(pow(frame_h, 2) + pow(col_y*2, 2)), 6], center = true);
    }
}

// 3. الخزان العلوي (15 لتر) مع حواجز منع التموج وفتحة الخروج
module top_tank_assembly() {
    z_pos = base_h + 12 + frame_h;

    // جسم الخزان الأكريليك
    color([0.85, 0.95, 1.0, 0.35])
    translate([-top_tank_w/2, -top_tank_d/2, z_pos])
        difference() {
            cube([top_tank_w, top_tank_d, top_tank_h]);
            translate([4, 4, 4])
                cube([top_tank_w - 8, top_tank_d - 8, top_tank_h]);
        }

    // عمود الماء (12.75 لتر قياسي = ارتفاع 170 مم)
    color([0.05, 0.55, 0.95, 0.55])
    translate([-top_tank_w/2 + 5, -top_tank_d/2 + 5, z_pos + 5])
        cube([top_tank_w - 10, top_tank_d - 10, 170]);

    // 3 حواجز داخلية مثقبة لمنع التموج
    color([1, 1, 1, 0.65])
    for (i = [-75, 0, 75]) {
        translate([i, -top_tank_d/2 + 6, z_pos + 25])
            cube([3, top_tank_d - 12, top_tank_h - 45]);
    }

    // وصلة مخرج الخزان السفلية
    color("Black")
    translate([0, -100, z_pos - 8])
        cylinder(h = 16, r = 12);
}

// 4. خزان التجميع السفلي (18 لتر)
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

// 5. الشبكة الهيدروليكية وصندوق التحكم الإلكتروني
module hydraulic_and_electronics() {
    z_top = base_h + 12 + frame_h;
    z_manifold = base_h + 12 + 950;   // = 980 mm

    // الخط الرئيسي النازل من الخزان (قطر 12 مم -> نصف قطر 6 مم)
    color("DodgerBlue")
    translate([0, -100, z_manifold])
        cylinder(h = z_top - z_manifold, r = 6);

    // فلتر المياه الرئيسي
    color("DarkCyan")
    translate([0, -100, z_top - 120])
        cylinder(h = 65, r = 18, center = true);

    // حساس التدفق الرئيسي (YF-S201)
    translate([0, -100, z_manifold + 320])
        detailed_flow_sensor(1.2, "Black");

    // حساس الضغط الرئيسي + Air Trap (z = 1160 ملم)
    translate([0, -100, z_manifold + 180])
        detailed_pressure_airtrap();

    // مشعب التوزيع الأفقي
    color("SteelBlue")
    translate([-135, -100, z_manifold])
        rotate([0, 90, 0]) cylinder(h = 270, r = 13);

    // الفروع الثلاثة (Zone A, Zone B, Zone C)
    zone_x = [-115, 0, 115];
    for (i = [0 : 2]) {
        x = zone_x[i];

        // أنبوب الفرع المستمر (قطر 8 مم -> نصف قطر 4.0 مم)
        color("DeepSkyBlue")
        translate([x, -100, base_h + 130])
            cylinder(h = z_manifold - (base_h + 130), r = 4.0);

        // 1. صمام العزل الكهربائي (12V Direct Acting)
        translate([x, -100, z_manifold - 70])
            rotate([0, 90, 0]) detailed_solenoid_valve();

        // مشبك تثبيت علوي على العارضة z = 850
        translate([x, -100, base_h + 12 + 850])
            pipe_clamp();

        // 2. حساس التدفق الفرعي (YF-S401)
        translate([x, -100, z_manifold - 180])
            detailed_flow_sensor(0.85, "White");

        // 3. حساس الضغط الفرعي + Air Trap (z = 720 ملم، فرق 0.44 م)
        translate([x, -100, z_manifold - 260])
            detailed_pressure_airtrap();

        // 4. نقطة التسرب التجريبية (محبس جانبي أحمر)
        color("Crimson")
        translate([x - 14, -100, z_manifold - 340])
            rotate([0, 90, 0]) cylinder(h = 20, r = 4);

        // مشبك تثبيت سفلي على العارضة z = 550
        translate([x, -100, base_h + 12 + 550])
            pipe_clamp();

        // 5. صمام الخنق الإبري النحاسي
        translate([x, -100, z_manifold - 460])
            detailed_needle_valve();
    }

    // --- صندوق التحكم الإلكتروني الشفاف ---
    box_x = 220;
    box_y = -110;
    box_z = z_manifold - 220;

    // جسم الصندوق الرمادي
    color("DimGray")
    translate([box_x, box_y, box_z])
        cube([70, 140, 220], center = true);

    // الغطاء الأمامي الشفاف
    color([0.9, 0.95, 1.0, 0.3])
    translate([box_x + 36, box_y, box_z])
        cube([3, 134, 214], center = true);

    // 1. لوحة ESP32
    color("Navy")
    translate([box_x + 25, box_y - 30, box_z + 55])
        cube([8, 28, 55], center = true);

    // 2. شريحة ADS1115
    color("DeepSkyBlue")
    translate([box_x + 25, box_y + 25, box_z + 65])
        cube([6, 18, 28], center = true);

    // 3. وحدة 4-Channel Relay Module
    color("DodgerBlue")
    translate([box_x + 22, box_y, box_z - 35])
        cube([18, 75, 55], center = true);

    // 4. شاشة OLED صغيرة (OLED: decision pending)
    //    احذف هذه الكتلة إذا اعتمدت BOM النهائية عدم شراء الشاشة.
    color("Black")
    translate([box_x + 32, box_y, box_z + 92])
        cube([4, 35, 18], center = true);
}

// =========================================================================
// تشغيل التجميع الكامل
// =========================================================================
base_plate();
aluminum_frame();
top_tank_assembly();
bottom_tank();
hydraulic_and_electronics();