// Three-point belt tension gauge body for a Phidgets 3133 (CZL635, 5 kg)
// micro load cell. Print in PETG/ABS/ASA, 4 walls, 40% infill.
//
// Layout (side view, belt runs left-right under the gauge):
//
//        fixed end bolted to the bridge          free end
//   [=====|o  o|=========== load cell ===========|o  o|]
//                                                  | plunger
//      (     )                                 (  v  )      ( anvil )
//   ----anvil--------------- belt ----------------v------------anvil----
//
// Both anvils and the plunger press the belt from the same side. Push until
// both anvils touch: the plunger then deflects the belt exactly `depth`.
// Extra hand force goes into the anvils, not the load cell.
//
// MEASURE YOUR CELL and set these first (calipers):
cell_len   = 55.25;   // overall length
cell_w     = 12.7;    // width = height
hole_d     = 5.5;     // clearance for the cell's threads (M5 -> 5.5; M4 -> 4.5)
hole_a     = 5.0;     // centre of the hole nearest the end, from that end
hole_b     = 15.0;    // centre of the second hole, from the same end
// gauge geometry (put the same numbers in the firmware: geom 40 1.5)
span       = 40;      // anvil spacing, centre to centre
depth      = 1.5;     // plunger tip past the anvil line
belt_w     = 9;       // belt width
anvil_r    = 3;
gap        = 2;       // clearance under the cell
wall       = 4;
guide      = 2.5;     // walls reach this far below the anvil line, to keep the gauge on the belt
$fn = 64;

inner = cell_w + 1.0;                   // channel width for the cell
hm    = (hole_a + hole_b) / 2;          // plunger sits under the free-end holes
cell_x0 = hm - cell_len;                // fixed end of the cell (x), free end at x = hm
cell_z0 = 2 * anvil_r + gap;            // cell bottom (anvil line is z = 0)
body_x0 = cell_x0 - 2;
body_x1 = max(span / 2 + anvil_r + 3, hm + 3);
top_z   = cell_z0 + cell_w + 3;         // bridge thickness above the cell

module anvil(x) {
    translate([x, 0, anvil_r]) rotate([90, 0, 0])
        cylinder(r = anvil_r, h = inner + 2 * wall, center = true);
}

module body() {
    difference() {
        union() {
            // side walls
            for (s = [-1, 1])
                translate([body_x0, s > 0 ? inner / 2 : -inner / 2 - wall, -guide])
                    cube([body_x1 - body_x0, wall, top_z + guide]);
            // anvils
            anvil(-span / 2);
            anvil(span / 2);
            // bridge over the fixed end; M5 screws go down through it into the cell
            translate([body_x0, -inner / 2, cell_z0 + cell_w])
                cube([hole_b + 5 - (body_x0 - cell_x0), inner, 3]);
            // grip on top
            translate([body_x0, -inner / 2 - wall, top_z])
                cube([body_x1 - body_x0, inner + 2 * wall, 4]);
        }
        // fixed-end screw holes through bridge and grip (screws go down into the cell)
        for (h = [hole_a, hole_b])
            translate([cell_x0 + h, 0, cell_z0]) cylinder(d = hole_d, h = 50);
        // clearance so nothing but the fixed end touches the cell
        translate([cell_x0 + hole_b + 5, -inner / 2, cell_z0 - gap / 2])
            cube([cell_len, inner, cell_w + gap]);
    }
}

// plunger: bolts up into the free-end holes, rounded nose at z = -depth
module plunger() {
    nose_z = -depth;
    difference() {
        hull() {
            translate([hm - hole_b - 4, -cell_w / 2 + 0.5, cell_z0 - 4])
                cube([hole_b - hole_a + 8, cell_w - 1, 4]);
            translate([hm, 0, nose_z + anvil_r]) rotate([90, 0, 0])
                cylinder(r = anvil_r, h = cell_w - 1, center = true);
        }
        // screw holes from below into the cell's free end (counterbored)
        for (h = [hole_a, hole_b])
            translate([hm - h, 0, -20]) {
                cylinder(d = hole_d, h = 40);
                cylinder(d = 9.5, h = 20 + cell_z0 - 3);
            }
    }
}

// Print the body on its side (rotate 90 about X) so the anvils print as
// round bumps, not overhangs; print the plunger nose-up.
// Mount the cell with its CABLE END as the fixed end.
body();
translate([0, 35, 0]) plunger();

// check: tip must sit `depth` below the anvil line
echo(str("plunger tip at z = ", -depth, " mm (anvils at 0)"));
