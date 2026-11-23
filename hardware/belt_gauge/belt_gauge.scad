// Three-point belt tension gauge body for a Phidgets 3133 (CZL635, 5 kg)
// micro load cell: 55.25 x 12.7 x 12.7 mm, one M5 threaded hole near each
// end, 40 mm centre to centre. Print in PETG/ABS/ASA, 4 walls, 40% infill.
//
// Side view (belt runs left-right under the gauge, z = 0 is the anvil line):
//
//    screw                                     screw (from below)
//   [==v=== load cell =============================v==]
//     fixed end, clamped                        free end + plunger
//                                                  |
//      (anvil)                                   ( v )          (anvil)
//   ------------------------ belt -----------------v---------------------
//
// Both anvils and the plunger press the belt from the same side. Push until
// both anvils touch: the plunger then deflects the belt exactly `depth`.
// Extra hand force goes into the anvils, not the load cell.
//
// Status: draft, not rendered here. Check in OpenSCAD before printing.

// ---- load cell (measured: holes 40 mm apart)
cell_len  = 55.25;
cell_w    = 12.7;            // width = height
hole_pitch = 40;
hole_d    = 5.5;             // M5 clearance
head_d    = 9.5;             // M5 socket head counterbore
// ---- gauge geometry (same numbers in the firmware: geom 40 1.5)
span      = 40;              // anvil spacing, centre to centre
depth     = 1.5;             // plunger tip past the anvil line
anvil_r   = 3;
gap       = 4;               // air under the cell (also gives the plunger room for its nut)
wall      = 4;
fit       = 0.3;             // total clearance where the cell is clamped
free_clr  = 1.2;             // total clearance along the rest of the cell
skirt     = 2.5;             // walls reach this far below the anvil line
$fn = 64;

end_off  = (cell_len - hole_pitch) / 2;   // hole centre from each end (7.6)
x_free   = 0;                             // free-end hole = plunger position
x_fixed  = -hole_pitch;                   // fixed-end hole
cell_x0  = x_fixed - end_off;             // cell ends
cell_x1  = x_free + end_off;
cell_z0  = 2 * anvil_r + gap;             // cell bottom
cell_z1  = cell_z0 + cell_w;              // cell top
inner    = cell_w + free_clr;
body_x0  = cell_x0 - 3;
body_x1  = span / 2 + anvil_r + 3;
clamp_len = 2 * end_off + 2;              // length of the clamped end
roof     = 4;                             // thickness above the cell

module anvil(x) {
    translate([x, 0, anvil_r]) rotate([90, 0, 0])
        cylinder(r = anvil_r, h = inner + 2 * wall, center = true);
}

module body() {
    difference() {
        union() {
            // side walls, full length
            for (s = [-1, 1])
                translate([body_x0, s > 0 ? inner / 2 : -inner / 2 - wall, -skirt])
                    cube([body_x1 - body_x0, wall, cell_z1 + roof + skirt]);
            anvil(-span / 2);
            anvil(span / 2);
            // roof over the clamped end
            translate([body_x0, -inner / 2, cell_z1])
                cube([clamp_len + (cell_x0 - body_x0), inner, roof]);
            // snug side pads at the clamped end: stop the cell turning on its one screw
            for (s = [-1, 1])
                translate([cell_x0, s > 0 ? (cell_w + fit) / 2 : -inner / 2, cell_z0])
                    cube([clamp_len, (inner - cell_w - fit) / 2, cell_w]);
            // end stop
            translate([body_x0, -inner / 2, cell_z0]) cube([cell_x0 - body_x0, inner, cell_w]);
            // grip bar along the top (with an air gap over the flexing part)
            translate([body_x0, -inner / 2 - wall, cell_z1 + roof])
                cube([body_x1 - body_x0, inner + 2 * wall, 4]);
        }
        // fixed-end screw: down through grip and roof into the cell
        translate([x_fixed, 0, cell_z1 - 1]) cylinder(d = hole_d, h = 30);
        translate([x_fixed, 0, cell_z1 + roof]) cylinder(d = head_d, h = 30);
        // cable exit at the fixed end
        translate([body_x0 - 1, -3, cell_z0 + 2]) cube([5, 6, 8]);
        // clearance for the plunger screw's head: nothing may touch it, or force
        // would bypass the load cell
        translate([x_free, 0, cell_z1]) cylinder(d = head_d + 2, h = 30);
    }
}

// plunger: an M5 x 20 screw goes DOWN through the cell's free-end hole into
// the plunger and is held by a nut in the plunger's side slot. Side lips hug
// the cell so it can't turn. Rounded nose, lowest point at z = -depth.
module plunger() {
    lip = 3;
    len = 2 * end_off - 1;
    nut_af = 8.2; nut_t = 4.2;          // M5 nut, with print clearance
    difference() {
        union() {
            hull() {
                translate([x_free - len / 2, -cell_w / 2, cell_z0 - 3])
                    cube([len, cell_w, 3]);
                translate([x_free, 0, -depth + anvil_r]) rotate([90, 0, 0])
                    cylinder(r = anvil_r, h = cell_w - 2, center = true);
            }
            for (s = [-1, 1])
                translate([x_free - len / 2, s > 0 ? cell_w / 2 + fit / 2 : -cell_w / 2 - fit / 2 - 1.5,
                           cell_z0 - 3])
                    cube([len, 1.5, 3 + lip]);
        }
        // screw clearance, stopping 2 mm above the nose tip
        translate([x_free, 0, -depth + 2]) cylinder(d = hole_d, h = 20);
        // nut slot from the side
        translate([x_free, 0, cell_z0 - 3 - nut_t - 0.5])
            hull() for (dy = [0, 10]) translate([0, dy, 0])
                cylinder(d = nut_af / cos(30), h = nut_t, $fn = 6);
    }
}

// Print the body on its side so the anvils print as bumps, not overhangs;
// print the plunger nose-up. Mount the cell with its CABLE END as the fixed end.
body();
translate([0, 35, 0]) plunger();

echo(str("hole from each end ", end_off, " mm; plunger tip at z = ", -depth,
         " (anvils at 0); cell clear of anvils by ", cell_z0 - 2 * anvil_r, " mm"));
