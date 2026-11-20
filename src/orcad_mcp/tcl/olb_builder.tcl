# orcad-mcp library builder: runs in Cadence's standalone tclsh (tools/tcltk/8.4/bin/tclsh.exe), not in Capture.
#
#   tclsh olb_builder.tcl build <orDb_Dll_Tcl.dll> <library.olb> <spec.tcl>
#   tclsh olb_builder.tcl list  <orDb_Dll_Tcl.dll> <library.olb> <spec.tcl>
#
# spec.tcl holds one Tcl list per part:
#   {name footprint value prefix {x1 y1 x2 y2} {{number name side pos} ...} template}
# side: L (left), R (right), B (bottom); pos: y for L/R, x for B; pins are 30 long outside the body.
# A new package is a copy of a template package with at least as many pins whose pins, body and pin
# numbers are replaced (the Tcl API of Capture 16.5 cannot create a package from nothing, and cannot add
# pin numbers to a device, only set or delete them). Every pin is passive.
# Output: one JSON object on stdout.

foreach {mode dll libPath specPath} $argv break
load $dll DboTclWriteBasic
proc ::NULL {args} { error "NULL object" }

proc cs {{v ""}} { if {$v eq ""} { return [DboTclHelper_sMakeCString] }; return [DboTclHelper_sMakeCString $v] }
proc str {c} { return [DboTclHelper_sGetConstCharPtr $c] }
proc pt {p} { return [list [DboTclHelper_sGetCPointX $p] [DboTclHelper_sGetCPointY $p]] }
proc js {s} { return "\"[string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $s]\"" }
proc jarr {items} { return "\[[join $items ,]\]" }

set ses [DboTclHelper_sCreateSession]
set st [DboState]
set lib [$ses GetLib [cs $libPath] $st]
if {$lib eq "NULL" || [$st OK] != 1} { error "cannot open library $libPath" }

proc packages {} {
    global lib st
    set out {}
    set it [$lib NewPackagesIter $st]
    set p [$it NextPackage $st]
    while {$p ne "NULL"} {
        set n [cs]; $p GetName $n
        lappend out [str $n] $p
        set p [$it NextPackage $st]
    }
    return $out
}

proc findpkg {name} {
    foreach {n p} [packages] { if {$n eq $name} { return $p } }
    return NULL
}

proc firstdevice {pk} {
    global st
    set it [$pk NewDevicesIter $st]
    return [$it NextDevice $st]
}

proc pincount {pk} {
    global st
    set dev [firstdevice $pk]
    if {$dev eq "NULL"} { return 0 }
    return [$dev GetPinCount $st]
}

proc devicecount {pk} {
    global st
    set n 0
    set it [$pk NewDevicesIter $st]
    set d [$it NextDevice $st]
    while {$d ne "NULL"} { incr n; set d [$it NextDevice $st] }
    return $n
}

# smallest single-device package with at least n pins
proc pick_template {n} {
    set best ""
    set bestCount 0
    foreach {name p} [packages] {
        if {[devicecount $p] != 1} { continue }
        set c [pincount $p]
        if {$c >= $n && ($best eq "" || $c < $bestCount)} {
            set best $name
            set bestCount $c
        }
    }
    if {$best eq ""} { error "no package with at least $n pins in the library to copy from" }
    return $best
}

proc pinnumbers {pk} {
    set dev [firstdevice $pk]
    set nums {}
    set it [$dev NewPinNumbersIter]
    set c [cs]
    while {1} {
        set r [$it NextNumber $c]
        if {[$r OK] != 1} { break }
        lappend nums [str $c]
    }
    return $nums
}

proc build {name footprint value prefix box pins template} {
    global lib st
    if {[findpkg $name] ne "NULL"} { error "$name already exists in the library" }
    if {$template eq ""} { set template [pick_template [llength $pins]] }
    set src [findpkg $template]
    if {$src eq "NULL"} { error "template $template not found" }
    if {[pincount $src] < [llength $pins]} { error "template $template has fewer than [llength $pins] pins" }
    set pk [$lib CopyPackageAll $src [cs $name] $st]
    if {[$st OK] != 1 || $pk eq "NULL"} { error "copying $template failed" }
    set pit [$pk NewPartsIter $st]
    set lp [$pit NextPart $st]
    if {$lp eq "NULL"} { error "$name: copied package has no part" }
    set vit [$lp NewVectorsIter $st $::IterDefs_ALL]
    set vecs {}
    set v [$vit NextVector $st]
    while {$v ne "NULL"} { lappend vecs $v; set v [$vit NextVector $st] }
    foreach v $vecs { $lp DeleteVector $v }
    $lp RemoveSymbolPins
    foreach {x1 y1 x2 y2} $box break
    $lp NewBox $st [DboTclHelper_sMakeCPoint $x1 $y1] [DboTclHelper_sMakeCPoint $x2 $y2]
    foreach p $pins {
        foreach {num pname side c} $p break
        switch -- $side {
            L { set ps [list $x1 $c]; set ph [list [expr {$x1 - 30}] $c] }
            R { set ps [list $x2 $c]; set ph [list [expr {$x2 + 30}] $c] }
            B { set ps [list $c $y2]; set ph [list $c [expr {$y2 + 30}]] }
            default { error "$name: bad side $side" }
        }
        set sp [$lp NewSymbolPinScalar $st [cs $pname] 4 \
            [DboTclHelper_sMakeCPoint [lindex $ps 0] [lindex $ps 1]] \
            [DboTclHelper_sMakeCPoint [lindex $ph 0] [lindex $ph 1]]]
        if {[$st OK] != 1 || $sp eq "NULL"} { error "$name: pin $num $pname failed" }
    }
    set dev [firstdevice $pk]
    set have [$dev GetPinCount $st]
    for {set i 0} {$i < [llength $pins]} {incr i} {
        $dev SetPinNumber $i [cs [lindex [lindex $pins $i] 0]]
    }
    for {set i [expr {$have - 1}]} {$i >= [llength $pins]} {incr i -1} {
        set r [$dev DeletePinNumber $i]
        if {[$r OK] != 1} { error "$name: DeletePinNumber $i failed" }
    }
    $pk SetPCBFootprint [cs $footprint]
    catch {$pk SetReferenceTemplate [cs $prefix]}
    catch {$lp SetReference [cs $prefix]}
    catch {$lp SetPartValue [cs $value]}
    $lib SavePackageAll $pk
    return $template
}

proc describe {name} {
    global st
    set pk [findpkg $name]
    if {$pk eq "NULL"} { return "{\"name\":[js $name],\"exists\":false}" }
    set fp [cs]; $pk GetPCBFootprint $fp
    set pins {}
    set pit [$pk NewPartsIter $st]
    set lp [$pit NextPart $st]
    set it [$lp NewLPinsIter $st]
    set pin [$it NextPin $st]
    while {$pin ne "NULL"} {
        set n [cs]; $pin GetPinName $n
        set s [pt [$pin GetStartPoint $st]]
        set h [pt [$pin GetHotSpot $st]]
        lappend pins "{\"name\":[js [str $n]],\"start\":[jarr $s],\"hot\":[jarr $h]}"
        set pin [$it NextPin $st]
    }
    set nums {}
    foreach n [pinnumbers $pk] { lappend nums [js $n] }
    return "{\"name\":[js $name],\"exists\":true,\"footprint\":[js [str $fp]],\"numbers\":[jarr $nums],\"pins\":[jarr $pins]}"
}

set f [open $specPath r]
set spec {}
foreach line [split [read $f] "
"] {
    if {[string trim $line] ne ""} { lappend spec $line }
}
close $f

if {$mode eq "build"} {
    set built {}
    foreach part $spec {
        foreach {name footprint value prefix box pins template} $part break
        set used [build $name $footprint $value $prefix $box $pins $template]
        lappend built "{\"name\":[js $name],\"template\":[js $used]}"
    }
    $ses SaveLib $lib
    puts "{\"built\":[jarr $built]}"
} elseif {$mode eq "list"} {
    set items {}
    foreach part $spec { lappend items [describe [lindex $part 0]] }
    set n [expr {[llength [packages]] / 2}]
    puts "{\"packages\":$n,\"parts\":[jarr $items]}"
} else {
    error "mode must be build or list"
}
