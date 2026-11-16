// =========================================================================
// التصميم الميكانيكي ثلاثي الأبعاد عالي التفاصيل (High-Detail 3D CAD)
// مشروع كشف وعزل تسربات المياه الذكي - تخصص هندسة الميكاترونيات 2026
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
// وحدات المكونات التفصيلية (Detailed Sub-Assemblies)
// =========================================================================

// 1. صمام العزل الكهربائي (12V Direct Acting Solenoid Valve)
module detailed_solenoid_valve() {
    // الجسم النحاسي السفلي
    color("Goldenrod") {
        cylinder(h = 24, r = 13, center = true);
        rotate([0, 90, 0]) cylinder(h = 46, r = 8, center = true); // فتحات الدخول والخروج
    }
    // الملف المغناطيسي العلوي (Coil)
    color("RoyalBlue")
    translate([0, 0, 22])
        cube([26, 26, 28], center = true);
    // غطاء التوصيل الكهربائي العلوي
    color("Black")
    translate([0, 0, 38])
        cube([16, 16, 6], center = true);
}

// 2. حساس التدفق التوربيني (YF-S201 / YF-S401)
module detailed_flow_sensor(scale_factor = 1.0, body_color = "White") {
    scale([scale_factor, scale_factor, scale_factor]) {
        color(body_color) {
            // غرفة التوربين الدائرية
            rotate([90, 0, 0]) cylinder(h = 18, r = 16, center = true);
            // أنبوب المرور
            cylinder(h = 54, r = 7, center = true);
        }
        // مستشعر Hall Effect العلوي + مخرج الأسلاك
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
    // أنبوب الهواء الشفاف (Air Trap)
    color([0.9, 0.95, 1.0, 0.5])
    translate([16, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 45, r = 4);
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
    // عمود الإبرة ومقبض التحكم الدائري (Knob)
    color("Silver")
    translate([22, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 15, r = 2.5);
    color("Crimson")
    translate([37, 0, 0])
        rotate([0, 90, 0]) cylinder(h = 6, r = 14);
}

// 5. مشبك تثبيت المواسير (Pipe Clamp)
module pipe_clamp() {
    color("Black") {
        difference() {
            cylinder(h = 12, r = 9, center = true);
            cylinder(h = 14, r = 5.5, center = true);
        }
        translate([0, 9, 0]) cube([16, 8, 12], center = true);
    }
}

// =========================================================================
// التجميع الرئيسي للمجسم (Main Structural & Hydraulic Assembly)
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

        // عارضتان أفقيتان لتثبيت المواسير والحساسات
    for (z_bar =[]) {
            translate([-col_x - profile/2, -col_y - profile/2, z_start + z_bar])
                cube([col_x*2 + profile, profile, profile]);
        }
    }

    // زوايا ربط معدنية في الأركان (Corner Brackets)
    color("DimGray")
    for (x = [-col_x + 15, col_x - 15])
        for (y = [-col_y, col_y])
            translate([x, y, z_start + 15])
                cube([18, 18, 18], center = true);

    // التدعيم القطري الجانبي (Cross Bracing)
    color("DarkSlateGray")
    for (x = [-col_x, col_x]) {
        translate([x, 0, z_start + frame_h/2])
            rotate([atan2(frame_h, col_y*2), 0, 0])
                cube([6, sqrt(pow(frame_h,2) + pow(col_y*2,2)), 6], center = true);
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

    // الماء بالداخل (12 لتر)
    color([0.05, 0.55, 0.95, 0.55])
    translate([-top_tank_w/2 + 5, -top_tank_d/2 + 5, z_pos + 5])
        cube([top_tank_w - 10, top_tank_d - 10, top_tank_h * 0.78]);

    // 3 حواجز داخلية مثقبة (Baffles)
    color([1, 1, 1, 0.65])
    for (i = [-75, 0, 75]) {
        translate([i, -top_tank_d/2 + 6, z_pos + 25])
            cube([3, top_tank_d - 12, top_tank_h - 45]);
    }

    // وصلة مخرج الخزان السفلية (Tank Bulkhead Fitting)
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
    z_manifold = base_h + 12 + 950;

    // الخط الرئيسي النازل من الخزان (12 مم)
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

    // حساس الضغط المرجعي (P_Main + Air Trap)
    translate([0, -100, z_manifold + 180])
        detailed_pressure_airtrap();

    // مشعب التوزيع الأفقي (Distribution Manifold)
    color("SteelBlue")
    translate([-135, -100, z_manifold])
        rotate([0, 90, 0]) cylinder(h = 270, r = 13);

    // الفروع الثلاثة (Zone A, Zone B, Zone C)
    zone_x = [-115, 0, 115];
    for (i = [0 : 2]) {
        x = zone_x[i];

        // أنبوب الفرع المستمر (8 مم)
        color("DeepSkyBlue")
        translate([x, -100, base_h + 130])
            cylinder(h = z_manifold - (base_h + 130), r = 4.5);

        // 1. صمام العزل الكهربائي (12V Direct Acting) عند المدخل
        translate([x, -100, z_manifold - 70])
            rotate([0, 90, 0]) detailed_solenoid_valve();

        // مشبك تثبيت علوي على العارضة
        translate([x, -112, base_h + 12 + 850])
            pipe_clamp();

        // 2. حساس التدفق الفرعي (YF-S401)
        translate([x, -100, z_manifold - 180])
            detailed_flow_sensor(0.85, "White");

        // 3. حساس الضغط الفرعي + Air Trap
        translate([x, -100, z_manifold - 260])
            detailed_pressure_airtrap();

        // 4. نقطة التسرب التجريبية (محبس جانبي أحمر)
        color("Crimson")
        translate([x - 14, -100, z_manifold - 340])
            rotate([0, 90, 0]) cylinder(h = 20, r = 4);

        // مشبك تثبيت سفلي على العارضة
        translate([x, -112, base_h + 12 + 550])
            pipe_clamp();

        // 5. صمام الخنق الإبري النحاسي (Brass Needle Valve)
        translate([x, -100, z_manifold - 460])
            detailed_needle_valve();
    }

    // --- صندوق التحكم الإلكتروني الشفاف (Control Enclosure) ---
    box_x = 225;
    box_y = -60;
    box_z = z_manifold - 220;

    // جسم الصندوق الرمادي
    color("DimGray")
    translate([box_x, box_y, box_z])
        cube([70, 140, 220], center = true);

    // الغطاء الأمامي الشفاف لرؤية الإلكترونيات
    color([0.9, 0.95, 1.0, 0.3])
    translate([box_x + 36, box_y, box_z])
        cube([3, 134, 214], center = true);

    // المكونات الداخلية داخل الصندوق:
    // 1. لوحة ESP32 (أزرق داكن)
    color("Navy")
    translate([box_x + 25, box_y - 30, box_z + 55])
        cube([8, 28, 55], center = true);

    // 2. شريحة ADS1115 (أزرق فاتح)
    color("DeepSkyBlue")
    translate([box_x + 25, box_y + 25, box_z + 65])
        cube([6, 18, 28], center = true);

    // 3. وحدة 4-Channel Relay Module (أزرق سماوي مع 4 ريلاي)
    color("DodgerBlue")
    translate([box_x + 22, box_y, box_z - 35])
        cube([18, 75, 55], center = true);
    // 4. شاشة OLED صغيرة في أعلى الصندوق لعرض الحالة
    color("Black")
    translate([box_x + 32, box_y, box_z + 92])
        cube([4, 35, 18], center = true);
}

// تشغيل التجميع الكامل
base_plate();
aluminum_frame();
top_tank_assembly();
bottom_tank();
hydraulic_and_electronics();


