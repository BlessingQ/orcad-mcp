# orcad-mcp helper library, sent to Capture by the orcad-mcp server through the bridge.
# Tcl 8.4: no {*}, dict, lassign, lmap, apply, "in"/"ni", namespace path.
#
# Entry point: ::orcadmcp::call <command> ?arg ...?   -> JSON text
# Every command is a proc ::orcadmcp::c_<command>.

namespace eval ::orcadmcp {
    variable lib_hash ""
    variable jmap {}
    # designs opened through open_design; kept when the library is reloaded
    variable opened
    if {![array exists opened]} {
        array set opened {}
    }
    variable pintypes {Input Bidirectional Output OpenCollector Passive 3State OpenEmitter Power}
}

# ---------------------------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------------------------

proc ::orcadmcp::jinit {} {
    variable jmap
    set jmap [list "\\" "\\\\" "\"" "\\\"" "\n" "\\n" "\r" "\\r" "\t" "\\t"]
    for {set i 0} {$i < 32} {incr i} {
        if {$i == 9 || $i == 10 || $i == 13} {
            continue
        }
        lappend jmap [format %c $i] [format "\\u%04x" $i]
    }
}
::orcadmcp::jinit

proc ::orcadmcp::js {s} {
    variable jmap
    return "\"[string map $jmap $s]\""
}

proc ::orcadmcp::jobj {args} {
    set parts {}
    foreach {k v} $args {
        lappend parts "[js $k]:$v"
    }
    return "\{[join $parts ,]\}"
}

proc ::orcadmcp::jarr {items} {
    return "\[[join $items ,]\]"
}

proc ::orcadmcp::jstrs {items} {
    set out {}
    foreach i $items {
        lappend out [js $i]
    }
    return [jarr $out]
}

proc ::orcadmcp::jbool {b} {
    if {$b} {
        return true
    }
    return false
}

proc ::orcadmcp::jnum {n} {
    if {[string is double -strict $n]} {
        return $n
    }
    return null
}

# ---------------------------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------------------------

proc ::orcadmcp::session {} {
    set s $::DboSession_s_pDboSession
    DboSession -this $s
    return $s
}

proc ::orcadmcp::getstr {obj method} {
    set c [DboTclHelper_sMakeCString]
    $obj $method $c
    return [DboTclHelper_sGetConstCharPtr $c]
}

# Result of a call that returns a DboState: 1 if OK. The state object is deleted.
proc ::orcadmcp::stok {st} {
    set ok 1
    catch {set ok [$st OK]}
    catch {$st -delete}
    return $ok
}

# {exists value} of an effective property (names are case-insensitive in Capture).
proc ::orcadmcp::prop {obj name} {
    set v [DboTclHelper_sMakeCString]
    if {[catch {$obj GetEffectivePropStringValue [DboTclHelper_sMakeCString $name] $v} st]} {
        return [list 0 ""]
    }
    if {![stok $st]} {
        return [list 0 ""]
    }
    return [list 1 [DboTclHelper_sGetConstCharPtr $v]]
}

# Flat list name value editable ... of all effective properties.
proc ::orcadmcp::effprops {obj} {
    set out {}
    set st [DboState]
    set it [$obj NewEffectivePropsIter $st]
    set n [DboTclHelper_sMakeCString]
    set v [DboTclHelper_sMakeCString]
    set t [DboTclHelper_sMakeDboValueType]
    set e [DboTclHelper_sMakeInt]
    set s [$it NextEffectiveProp $n $v $t $e]
    while {[$s OK] == 1} {
        catch {$s -delete}
        lappend out [DboTclHelper_sGetConstCharPtr $n] [DboTclHelper_sGetConstCharPtr $v] \
            [DboTclHelper_sGetInt $e]
        set s [$it NextEffectiveProp $n $v $t $e]
    }
    catch {$s -delete}
    delete_DboEffectivePropsIter $it
    $st -delete
    return $out
}

proc ::orcadmcp::designs {} {
    set st [DboState]
    set ses [session]
    set it [$ses NewDesignsIter $st]
    set out {}
    set d [$it NextDesign $st]
    while {$d ne "NULL"} {
        lappend out $d
        set d [$it NextDesign $st]
    }
    delete_DboSessionDesignsIter $it
    $st -delete
    return $out
}

proc ::orcadmcp::active_design {} {
    set a NULL
    catch {set a [GetActivePMDesign]}
    if {$a eq "" || $a eq "NULL"} {
        set a [[session] GetActiveDesign]
    }
    return $a
}

# Resolve a design spec: "" = active design, else full path, file name (with or without .dsn)
# or a unique part of the file name, case-insensitive.
proc ::orcadmcp::design {spec} {
    if {$spec eq ""} {
        set a [active_design]
        if {$a eq "" || $a eq "NULL"} {
            error "No design is open in Capture. Open one, or call open_design with a .dsn path."
        }
        return $a
    }
    set want [string tolower [string map {/ \\} $spec]]
    set exact {}
    set base {}
    set part {}
    set all {}
    foreach d [designs] {
        set name [getstr $d GetName]
        set tail [string tolower [file tail $name]]
        lappend all [file tail $name]
        if {[string tolower $name] eq $want} {
            lappend exact $d $name
        }
        if {$tail eq $want || [file rootname $tail] eq $want} {
            lappend base $d $name
        }
        if {[string first $want $tail] >= 0} {
            lappend part $d $name
        }
    }
    foreach cand [list $exact $base $part] {
        if {[llength $cand] == 2} {
            return [lindex $cand 0]
        }
        if {[llength $cand] > 2} {
            set names {}
            foreach {x n} $cand {
                lappend names $n
            }
            error "design '$spec' is ambiguous: [join $names {; }]"
        }
    }
    if {[llength $all] == 0} {
        error "No design is open in Capture."
    }
    error "no open design matches '$spec'. Open designs: [join $all {; }]"
}

# Flat list: schematicName pageName page ...
proc ::orcadmcp::pages {d} {
    set st [DboState]
    set out {}
    set sit [$d NewViewsIter $st $::IterDefs_SCHEMATICS]
    set v [$sit NextView $st]
    while {$v ne "NULL"} {
        set sch [DboViewToDboSchematic $v]
        set sname [getstr $sch GetName]
        set pit [$sch NewPagesIter $st]
        set pg [$pit NextPage $st]
        while {$pg ne "NULL"} {
            lappend out $sname [getstr $pg GetName] $pg
            set pg [$pit NextPage $st]
        }
        delete_DboSchematicPagesIter $pit
        set v [$sit NextView $st]
    }
    delete_DboLibViewsIter $sit
    $st -delete
    return $out
}

# Flat list: schematicName pageName placedInst ...
proc ::orcadmcp::placed {d} {
    set st [DboState]
    set out {}
    foreach {sname pname pg} [pages $d] {
        set it [$pg NewPartInstsIter $st]
        set i [$it NextPartInst $st]
        while {$i ne "NULL"} {
            set p [DboPartInstToDboPlacedInst $i]
            if {$p ne "NULL"} {
                lappend out $sname $pname $p
            }
            set i [$it NextPartInst $st]
        }
        delete_DboPagePartInstsIter $it
    }
    $st -delete
    return $out
}

# Placed parts whose designator (U1A) or package reference (U1) equals ref.
proc ::orcadmcp::find_parts {d ref} {
    set want [string toupper $ref]
    set out {}
    foreach {sname pname p} [placed $d] {
        if {[string toupper [getstr $p GetReferenceDesignator]] eq $want
            || [string toupper [getstr $p GetReference]] eq $want} {
            lappend out $sname $pname $p
        }
    }
    return $out
}

# Flat list: netName {ref pinNumber pinName ...} ... for every flat net of the design.
proc ::orcadmcp::flatnets {d} {
    set st [DboState]
    set out {}
    set it [$d NewFlatNetsIter $st]
    set fn [$it NextFlatNet $st]
    while {$fn ne "NULL"} {
        set pins {}
        set pit [$fn NewPortOccurrencesIter $st]
        set po [$pit NextPortOccurrence $st]
        while {$po ne "NULL"} {
            set pi [$po GetPortInst $st]
            if {$pi ne "NULL"} {
                set owner [DboPartInstToDboPlacedInst [$pi GetOwner]]
                if {$owner ne "NULL"} {
                    set ref [getstr $owner GetReference]
                    set io NULL
                    catch {set io [$po GetInstance $st]}
                    if {$io ne "NULL" && $io ne ""} {
                        catch {
                            set r [getstr $io GetReference]
                            if {$r ne ""} {
                                set ref $r
                            }
                        }
                    }
                    lappend pins $ref [getstr $pi GetPinNumber] [getstr $pi GetPinName]
                }
            }
            set po [$pit NextPortOccurrence $st]
        }
        delete_DboFlatNetPortOccurrencesIter $pit
        lappend out [getstr $fn GetName] $pins
        set fn [$it NextFlatNet $st]
    }
    delete_DboDesignFlatNetsIter $it
    $st -delete
    return $out
}

proc ::orcadmcp::set_prop {p name value} {
    set st [$p SetEffectivePropStringValue [DboTclHelper_sMakeCString $name] \
        [DboTclHelper_sMakeCString $value]]
    if {![stok $st]} {
        error "Capture refused to set '$name' to '$value'"
    }
}

proc ::orcadmcp::redraw {} {
    catch {Menu "View::Zoom::Redraw"}
}

# ---------------------------------------------------------------------------------------------
# Commands (each returns JSON)
# ---------------------------------------------------------------------------------------------

proc ::orcadmcp::c_status {} {
    variable opened
    set st [DboState]
    set active ""
    set a [active_design]
    if {$a ne "" && $a ne "NULL"} {
        set active [getstr $a GetName]
    }
    set items {}
    foreach d [designs] {
        set name [getstr $d GetName]
        lappend items [jobj path [js $name] name [js [file tail $name]] \
            active [jbool [string equal -nocase $name $active]] \
            modified [jbool [$d IsModified $st]] \
            opened_by_mcp [jbool [info exists opened([string toupper $name])]]]
    }
    $st -delete
    set product ""
    catch {set product [GetProductVersion]}
    set bridge "unknown"
    catch {set bridge $::orcadmcp::bridge::version}
    return [jobj capture [js $product] tcl [js [info patchlevel]] bridge [js $bridge] \
        active_design [js $active] designs [jarr $items]]
}

proc ::orcadmcp::c_resolve {spec} {
    set d [design $spec]
    set st [DboState]
    set m [$d IsModified $st]
    $st -delete
    return [jobj path [js [getstr $d GetName]] modified [jbool $m]]
}

proc ::orcadmcp::c_pages {spec} {
    set d [design $spec]
    set st [DboState]
    set items {}
    foreach {sname pname pg} [pages $d] {
        set n 0
        set it [$pg NewPartInstsIter $st]
        set i [$it NextPartInst $st]
        while {$i ne "NULL"} {
            if {[DboPartInstToDboPlacedInst $i] ne "NULL"} {
                incr n
            }
            set i [$it NextPartInst $st]
        }
        delete_DboPagePartInstsIter $it
        lappend items [jobj schematic [js $sname] page [js $pname] parts $n]
    }
    $st -delete
    return [jobj design [js [getstr $d GetName]] root [js [getstr $d GetRootName]] pages [jarr $items]]
}

# props: extra property names to report (null when the part does not have it)
proc ::orcadmcp::c_parts {spec props} {
    set d [design $spec]
    set cols [concat [list refdes reference value footprint page schematic part] $props]
    set rows {}
    foreach {sname pname p} [placed $d] {
        set row [list [js [getstr $p GetReferenceDesignator]] [js [getstr $p GetReference]] \
            [js [lindex [prop $p Value] 1]] [js [lindex [prop $p "PCB Footprint"] 1]] \
            [js $pname] [js $sname] [js [getstr $p GetSourcePartName]]]
        foreach k $props {
            set pv [prop $p $k]
            if {[lindex $pv 0]} {
                lappend row [js [lindex $pv 1]]
            } else {
                lappend row null
            }
        }
        lappend rows [jarr $row]
    }
    return [jobj design [js [getstr $d GetName]] columns [jstrs $cols] rows [jarr $rows]]
}

proc ::orcadmcp::c_part {spec ref} {
    variable pintypes
    set d [design $spec]
    set found [find_parts $d $ref]
    if {[llength $found] == 0} {
        error "part '$ref' not found in [file tail [getstr $d GetName]]"
    }
    # pin -> flat net, keyed by "REF.PIN"
    array set net {}
    foreach {nname pins} [flatnets $d] {
        foreach {r num pname} $pins {
            set net([string toupper $r].$num) $nname
        }
    }
    set st [DboState]
    set items {}
    foreach {sname pname p} $found {
        set reference [getstr $p GetReference]
        set props {}
        set readonly {}
        foreach {n v e} [effprops $p] {
            lappend props $n [js $v]
            if {!$e} {
                lappend readonly $n
            }
        }
        set pins {}
        set it [$p NewPinsIter $st]
        set pin [$it NextPin $st]
        while {$pin ne "NULL"} {
            set num [getstr $pin GetPinNumber]
            set key [string toupper $reference].$num
            set nn null
            if {[info exists net($key)]} {
                set nn [js $net($key)]
            }
            set ty [$pin GetPinType $st]
            if {[string is integer -strict $ty] && $ty >= 0 && $ty < [llength $pintypes]} {
                set ty [lindex $pintypes $ty]
            }
            lappend pins [jobj number [js $num] name [js [getstr $pin GetPinName]] net $nn \
                type [js $ty] no_connect [jbool [$pin GetIsNoConnect $st]]]
            set pin [$it NextPin $st]
        }
        delete_DboPartInstPinsIter $it
        set loc [$p GetLocation $st]
        lappend items [jobj refdes [js [getstr $p GetReferenceDesignator]] reference [js $reference] \
            page [js $pname] schematic [js $sname] part [js [getstr $p GetSourcePartName]] \
            package [js [getstr $p GetSourcePackageName]] library [js [getstr $p GetSourceLibName]] \
            location [jarr [list [jnum [DboTclHelper_sGetCPointX $loc]] [jnum [DboTclHelper_sGetCPointY $loc]]]] \
            rotation [jnum [$p GetRotation $st]] \
            properties [eval jobj $props] read_only [jstrs $readonly] pins [jarr $pins]]
    }
    $st -delete
    return [jobj design [js [getstr $d GetName]] parts [jarr $items]]
}

proc ::orcadmcp::c_nets {spec} {
    set d [design $spec]
    set items {}
    foreach {nname pins} [flatnets $d] {
        set pl {}
        foreach {r num pname} $pins {
            lappend pl [jarr [list [js $r] [js $num] [js $pname]]]
        }
        lappend items [jobj name [js $nname] pins [jarr $pl]]
    }
    return [jobj design [js [getstr $d GetName]] nets [jarr $items]]
}

# changes: list of {refdes property value}. All-or-nothing: on any failure the applied changes
# are rolled back.
proc ::orcadmcp::c_set_props {spec changes redraw} {
    set d [design $spec]
    # validate everything first
    set plan {}
    foreach ch $changes {
        if {[llength $ch] != 3} {
            error "each change needs refdes, property and value: $ch"
        }
        set ref [lindex $ch 0]
        set name [lindex $ch 1]
        set value [lindex $ch 2]
        if {[string trim $name] eq ""} {
            error "empty property name for $ref"
        }
        set found [find_parts $d $ref]
        if {[llength $found] == 0} {
            error "part '$ref' not found in [file tail [getstr $d GetName]]"
        }
        foreach {sname pname p} $found {
            set exists 0
            set old ""
            set realname $name
            foreach {n v e} [effprops $p] {
                if {[string equal -nocase $n $name]} {
                    if {!$e} {
                        error "property '$n' of $ref is read-only"
                    }
                    set exists 1
                    set old $v
                    set realname $n
                    break
                }
            }
            lappend plan [list $p [getstr $p GetReferenceDesignator] $realname $value $exists $old]
        }
    }
    # apply
    set done {}
    foreach item $plan {
        foreach {p r name value exists old} $item break
        if {[catch {set_prop $p $name $value} err]} {
            for {set i [expr {[llength $done] - 1}]} {$i >= 0} {incr i -1} {
                foreach {up ur un uv ue uo} [lindex $done $i] break
                if {$ue} {
                    catch {set_prop $up $un $uo}
                } else {
                    catch {$up DeleteEffectiveProp [DboTclHelper_sMakeCString $un]}
                }
            }
            error "$r: $err (all changes of this call were rolled back)"
        }
        lappend done $item
    }
    set out {}
    foreach item $done {
        foreach {p r name value exists old} $item break
        set now [lindex [prop $p $name] 1]
        if {$exists} {
            set o [js $old]
        } else {
            set o null
        }
        lappend out [jobj refdes [js $r] property [js $name] old $o new [js $now] \
            created [jbool [expr {!$exists}]]]
    }
    if {$redraw} {
        redraw
    }
    set st [DboState]
    set m [$d IsModified $st]
    $st -delete
    return [jobj design [js [getstr $d GetName]] modified [jbool $m] changes [jarr $out]]
}

proc ::orcadmcp::c_save {spec} {
    set d [design $spec]
    set name [getstr $d GetName]
    set ses [session]
    catch {$ses MarkAllLibForSave $d}
    if {![stok [$ses SaveDesign $d]]} {
        error "Capture could not save $name (read-only file or locked by another program?)"
    }
    return [jobj path [js $name] saved true]
}

proc ::orcadmcp::c_open {path} {
    variable opened
    set before {}
    foreach d [designs] {
        lappend before [string toupper [getstr $d GetName]]
    }
    set st [DboState]
    set d [[session] GetDesignAndSchematics [DboTclHelper_sMakeCString $path] $st]
    set ok [stok $st]
    if {$d eq "NULL" || !$ok} {
        error "Capture could not open design $path"
    }
    set name [getstr $d GetName]
    set already [expr {[lsearch -exact $before [string toupper $name]] >= 0}]
    if {!$already} {
        set opened([string toupper $name]) 1
    }
    return [jobj path [js $name] already_open [jbool $already]]
}

proc ::orcadmcp::c_close {spec discard} {
    variable opened
    set d [design $spec]
    set name [getstr $d GetName]
    if {![info exists opened([string toupper $name])]} {
        error "$name was not opened by orcad-mcp; close it in Capture instead"
    }
    set st [DboState]
    set m [$d IsModified $st]
    $st -delete
    if {$m && !$discard} {
        error "$name has unsaved changes; save_design first or pass discard=true"
    }
    [session] RemoveDesign $d
    unset opened([string toupper $name])
    return [jobj path [js $name] closed true]
}

# ---------------------------------------------------------------------------------------------
# Page editing
# ---------------------------------------------------------------------------------------------
# Capture's GUI commands (PlacePart, PlaceWire, ...) act on the schematic page shown in the active
# window and take coordinates in that page's units: millimetres on a metric page, inches otherwise.
# Everything here takes and reports database units (1/100 inch) and converts.
#
# Calls that crash Capture 16.5 with an access violation (Tcl catch cannot stop them):
#  - a method called on a NULL object, e.g. [$pin GetNet $st] GetName $s on an unconnected pin
#    (::NULL below turns that into a Tcl error);
#  - undocumented GUI commands called without their arguments (GetWindows, GetActiveWindow, ...).
# Only commands whose signatures are known are used: the "Declare Sub" list inside Capture.exe
# (PlacePart x y lib pkg device convert, PlaceWire x1 y1 x2 y2, PlaceOffPage x y lib sym name, ...)
# and menu command ids from its menu resources.

namespace eval ::orcadmcp {
    variable CMD_NEXT_PAGE 17751
    variable CMD_PREV_PAGE 17752
    # hotspot offset (dx dy) from the placement point, per CAPSYM symbol, rotation 0
    variable symoffset
    array set symoffset {
        OFFPAGELEFT-L {0 10}
        OFFPAGELEFT-R {10 10}
        VCC_ARROW {10 10}
        VCC_BAR {10 10}
        VCC_CIRCLE {10 10}
        GND {10 0}
        GND_SIGNAL {10 0}
        GND_POWER {10 0}
        GND_EARTH {10 0}
    }
}

if {[info commands ::NULL] eq ""} {
    proc ::NULL {args} {
        error "orcad-mcp: '[lindex $args 0]' called on a NULL object; check Dbo results for NULL before using them"
    }
}

proc ::orcadmcp::pt {p} {
    return [list [DboTclHelper_sGetCPointX $p] [DboTclHelper_sGetCPointY $p]]
}

proc ::orcadmcp::rect {r} {
    return [concat [pt [DboTclHelper_sGetCRectTopLeft $r]] [pt [DboTclHelper_sGetCRectBottomRight $r]]]
}

proc ::orcadmcp::jpt {p} {
    return [jarr [list [jnum [lindex $p 0]] [jnum [lindex $p 1]]]]
}

proc ::orcadmcp::jrect {r} {
    set out {}
    foreach v $r {
        lappend out [jnum $v]
    }
    return [jarr $out]
}

proc ::orcadmcp::capsym {} {
    set dir [file dirname [info nameofexecutable]]
    foreach f [list [file join $dir library capsym.olb] [file join $dir .. capture library capsym.olb]] {
        if {[file exists $f]} {
            return [file nativename [file normalize $f]]
        }
    }
    error "capsym.olb not found next to [info nameofexecutable]; pass the symbol library explicitly"
}

proc ::orcadmcp::pagenum {pg} {
    set st [DboState]
    set n [$pg GetPageNumber $st]
    $st -delete
    return $n
}

# GUI coordinate (mm or inch) of a database coordinate on page pg
proc ::orcadmcp::gu {pg v} {
    if {[$pg GetIsMetric]} {
        return [format %.4f [expr {$v * 0.254}]]
    }
    return [format %.4f [expr {$v / 100.0}]]
}

# {schematic page pageObj} for a page name ("RS485") or "schematic/page"
proc ::orcadmcp::find_page {d name} {
    set hits {}
    foreach {sname pname pg} [pages $d] {
        if {[string equal -nocase $pname $name] || [string equal -nocase "$sname/$pname" $name]} {
            lappend hits $sname $pname $pg
        }
    }
    if {[llength $hits] == 0} {
        set names {}
        foreach {sname pname pg} [pages $d] {
            lappend names $pname
        }
        error "page '$name' not found. Pages: [join $names {, }]"
    }
    if {[llength $hits] > 3} {
        error "page name '$name' is in several schematic folders; use schematic/page"
    }
    return $hits
}

# Wires of a page: {wireObj start end netName} ...
proc ::orcadmcp::page_wires {pg} {
    set st [DboState]
    set out {}
    set it [$pg NewWiresIter $st]
    set w [$it NextWire $st]
    while {$w ne "NULL"} {
        set n [DboTclHelper_sMakeCString]
        catch {$w GetNetName $n}
        lappend out $w [pt [$w GetStartPoint $st]] [pt [$w GetEndPoint $st]] [DboTclHelper_sGetConstCharPtr $n]
        set w [$it NextWire $st]
    }
    delete_DboPageWiresIter $it
    $st -delete
    return $out
}

proc ::orcadmcp::near {a b} {
    return [expr {abs([lindex $a 0] - [lindex $b 0]) <= 1 && abs([lindex $a 1] - [lindex $b 1]) <= 1}]
}

# The page shown in the active window, as {design schematic page pageObj}, or {} when the active window
# is not a schematic page. Places a short marker wire in the page corner (0.508,0.508)-(2.54,0.508)
# (millimetres or inches, whatever the page uses), finds the page that received it and deletes it again.
proc ::orcadmcp::active_page {} {
    catch {UnSelectAll}
    PlaceWire 0.508 0.508 2.54 0.508
    catch {UnSelectAll}
    set marks {}
    foreach d [designs] {
        foreach {sname pname pg} [pages $d] {
            if {[$pg GetIsMetric]} {
                set a {2 2}
                set b {10 2}
            } else {
                set a {51 51}
                set b {254 51}
            }
            foreach {w p1 p2 net} [page_wires $pg] {
                if {([near $p1 $a] && [near $p2 $b]) || ([near $p1 $b] && [near $p2 $a])} {
                    lappend marks $d $sname $pname $pg $w
                }
            }
        }
    }
    foreach {d sname pname pg w} $marks {
        catch {$pg DeleteWire $w}
    }
    redraw
    if {[llength $marks] == 0} {
        return {}
    }
    return [lrange $marks 0 3]
}

# Error unless pg is the page in the active window.
proc ::orcadmcp::require_active {d pg pname} {
    set cur [active_page]
    if {[llength $cur] == 0} {
        error "No schematic page is shown in Capture's active window. Open page $pname (or call goto_page)."
    }
    if {[lindex $cur 3] ne $pg} {
        error "Capture shows page [lindex $cur 2], not $pname. Call goto_page first."
    }
}

proc ::orcadmcp::inst_set {pg} {
    set st [DboState]
    set out {}
    set it [$pg NewPartInstsIter $st]
    set i [$it NextPartInst $st]
    while {$i ne "NULL"} {
        set p [DboPartInstToDboPlacedInst $i]
        if {$p ne "NULL"} {
            lappend out $p
        }
        set i [$it NextPartInst $st]
    }
    delete_DboPagePartInstsIter $it
    $st -delete
    return $out
}

proc ::orcadmcp::jpins {p} {
    set st [DboState]
    set out {}
    set it [$p NewPinsIter $st]
    set pin [$it NextPin $st]
    while {$pin ne "NULL"} {
        lappend out [jobj number [js [getstr $pin GetPinNumber]] name [js [getstr $pin GetPinName]] \
            at [jpt [pt [$pin GetOffsetHotSpot $st]]] no_connect [jbool [$pin GetIsNoConnect $st]]]
        set pin [$it NextPin $st]
    }
    delete_DboPartInstPinsIter $it
    $st -delete
    return [jarr $out]
}

proc ::orcadmcp::symbols {pg} {
    set st [DboState]
    set out {}
    set it [$pg NewGlobalsIter $st]
    set g [$it NextGlobal $st]
    while {$g ne "NULL"} {
        lappend out global $g [getstr $g GetName] [pt [$g GetLocation $st]] [$g GetRotation $st] \
            [rect [$g GetBoundingBox]]
        set g [$it NextGlobal $st]
    }
    delete_DboPageGlobalsIter $it
    set it [$pg NewOffPageConnectorsIter $st]
    set o [$it NextOffPageConnector $st]
    while {$o ne "NULL"} {
        lappend out offpage $o [getstr $o GetName] [pt [$o GetLocation $st]] [$o GetRotation $st] \
            [rect [$o GetBoundingBox]]
        set o [$it NextOffPageConnector $st]
    }
    delete_DboPageOffPageConnectorsIter $it
    $st -delete
    return $out
}

proc ::orcadmcp::overlaps {r x1 y1 x2 y2} {
    foreach {a b c e} $r break
    return [expr {$c >= $x1 && $a <= $x2 && $e >= $y1 && $b <= $y2}]
}

proc ::orcadmcp::c_active_page {} {
    set cur [active_page]
    if {[llength $cur] == 0} {
        return [jobj design null schematic null page null]
    }
    foreach {d sname pname pg} $cur break
    return [jobj design [js [getstr $d GetName]] schematic [js $sname] page [js $pname] \
        number [jnum [pagenum $pg]] metric [jbool [$pg GetIsMetric]]]
}

# Move Capture's active window to another page of the same schematic folder with View > Next Page /
# Previous Page (F10 / Shift+F10), which follow the page numbers.
proc ::orcadmcp::c_goto_page {spec name} {
    variable CMD_NEXT_PAGE
    variable CMD_PREV_PAGE
    set d [design $spec]
    foreach {tsname tpname tpg} [find_page $d $name] break
    set cur [active_page]
    if {[llength $cur] == 0} {
        error "Open any schematic page of [file tail [getstr $d GetName]] in Capture first; goto_page steps from the page that is shown."
    }
    foreach {cd csname cpname cpg} $cur break
    if {$cd ne $d} {
        error "Capture shows a page of [file tail [getstr $cd GetName]]; open a page of [file tail [getstr $d GetName]] first."
    }
    if {$csname ne $tsname} {
        error "goto_page moves within one schematic folder ($csname -> $tsname is not supported)"
    }
    set diff [expr {[pagenum $tpg] - [pagenum $cpg]}]
    set cmd [expr {$diff > 0 ? $CMD_NEXT_PAGE : $CMD_PREV_PAGE}]
    for {set i 0} {$i < abs($diff)} {incr i} {
        if {[catch {MenuCommand $cmd} err]} {
            error "Capture refused page step [expr {$i + 1}] of [expr {abs($diff)}]: $err"
        }
    }
    set now [active_page]
    set shown ""
    if {[llength $now]} {
        set shown [lindex $now 2]
    }
    return [jobj design [js [getstr $d GetName]] from [js $cpname] page [js $shown] steps $diff \
        ok [jbool [string equal $shown $tpname]]]
}

proc ::orcadmcp::c_page_objects {spec name} {
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    set st [DboState]
    set parts {}
    foreach p [inst_set $pg] {
        lappend parts [jobj refdes [js [getstr $p GetReferenceDesignator]] part [js [getstr $p GetSourcePartName]] \
            location [jpt [pt [$p GetLocation $st]]] rotation [jnum [$p GetRotation $st]] \
            bbox [jrect [rect [$p GetBoundingBox]]] pins [jpins $p]]
    }
    set syms {}
    foreach {kind o sn loc rot bb} [symbols $pg] {
        lappend syms [jobj kind [js $kind] name [js $sn] location [jpt $loc] rotation [jnum $rot] bbox [jrect $bb]]
    }
    set wires {}
    foreach {w p1 p2 net} [page_wires $pg] {
        lappend wires [jobj net [js $net] start [jpt $p1] end [jpt $p2]]
    }
    set w ""
    set h ""
    if {[catch {set w [$pg GetPageWidth]}]} {
        catch {set w [$pg GetPageWidth $st]}
    }
    if {[catch {set h [$pg GetPageHeight]}]} {
        catch {set h [$pg GetPageHeight $st]}
    }
    $st -delete
    return [jobj design [js [getstr $d GetName]] schematic [js $sname] page [js $pname] \
        number [jnum [pagenum $pg]] metric [jbool [$pg GetIsMetric]] \
        size [jarr [list [jnum $w] [jnum $h]]] \
        parts [jarr $parts] symbols [jarr $syms] wires [jarr $wires]]
}

# Place a library part with its placement point at (x y); set reference, value and footprint.
proc ::orcadmcp::c_place_part {spec name lib part x y ref value footprint} {
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    require_active $d $pg $pname
    set before [inst_set $pg]
    PlacePart [gu $pg $x] [gu $pg $y] $lib $part "" 0
    catch {UnSelectAll}
    set new {}
    foreach p [inst_set $pg] {
        if {[lsearch -exact $before $p] < 0} {
            lappend new $p
        }
    }
    if {[llength $new] != 1} {
        error "Capture did not place '$part' from $lib (is the library path and part name right?)"
    }
    set p [lindex $new 0]
    if {$ref ne ""} {
        set_prop $p "Part Reference" $ref
    }
    if {$value ne ""} {
        set_prop $p "Value" $value
    }
    if {$footprint ne ""} {
        set_prop $p "PCB Footprint" $footprint
    }
    set st [DboState]
    set loc [pt [$p GetLocation $st]]
    $st -delete
    return [jobj refdes [js [getstr $p GetReferenceDesignator]] page [js $pname] location [jpt $loc] \
        pins [jpins $p]]
}

# points: {x y} ...  -> one wire per segment
proc ::orcadmcp::c_place_wire {spec name points} {
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    if {[llength $points] < 2} {
        error "a wire needs at least two points"
    }
    require_active $d $pg $pname
    for {set i 1} {$i < [llength $points]} {incr i} {
        set a [lindex $points [expr {$i - 1}]]
        set b [lindex $points $i]
        if {[lindex $a 0] != [lindex $b 0] && [lindex $a 1] != [lindex $b 1]} {
            error "segment $a -> $b is not horizontal or vertical"
        }
        PlaceWire [gu $pg [lindex $a 0]] [gu $pg [lindex $a 1]] [gu $pg [lindex $b 0]] [gu $pg [lindex $b 1]]
    }
    catch {UnSelectAll}
    set nets {}
    foreach {w p1 p2 net} [page_wires $pg] {
        foreach q $points {
            if {[near $p1 $q] || [near $p2 $q]} {
                if {[lsearch -exact $nets $net] < 0} {
                    lappend nets $net
                }
            }
        }
    }
    set st [DboState]
    set first [$pg IsConnectionPoint [DboTclHelper_sMakeCPoint [lindex [lindex $points 0] 0] [lindex [lindex $points 0] 1]] $st]
    set last [$pg IsConnectionPoint [DboTclHelper_sMakeCPoint [lindex [lindex $points end] 0] [lindex [lindex $points end] 1]] $st]
    $st -delete
    return [jobj page [js $pname] segments [expr {[llength $points] - 1}] nets [jstrs $nets] \
        ends_connected [jarr [list [jbool $first] [jbool $last]]]]
}

# kind: offpage | power | ground. (x y) is the connection point (hotspot).
proc ::orcadmcp::c_place_symbol {spec name kind netname x y symbol lib offset} {
    variable symoffset
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    if {$lib eq ""} {
        set lib [capsym]
    }
    if {[llength $offset] != 2 || ![string is integer -strict [lindex $offset 0]]
        || ![string is integer -strict [lindex $offset 1]]} {
        if {![info exists symoffset([string toupper $symbol])]} {
            error "unknown hotspot offset for symbol '$symbol'; pass offset"
        }
        set offset $symoffset([string toupper $symbol])
    }
    switch -- $kind {
        offpage {set cmd PlaceOffPage}
        power {set cmd PlacePower}
        ground {set cmd PlaceGround}
        default {error "kind must be offpage, power or ground"}
    }
    require_active $d $pg $pname
    set lx [expr {$x - [lindex $offset 0]}]
    set ly [expr {$y - [lindex $offset 1]}]
    $cmd [gu $pg $lx] [gu $pg $ly] $lib $symbol $netname
    catch {UnSelectAll}
    set st [DboState]
    set conn [$pg IsConnectionPoint [DboTclHelper_sMakeCPoint $x $y] $st]
    $st -delete
    set net ""
    foreach {w p1 p2 n} [page_wires $pg] {
        if {[near $p1 [list $x $y]] || [near $p2 [list $x $y]]} {
            set net $n
        }
    }
    return [jobj page [js $pname] kind [js $kind] name [js $netname] at [jpt [list $x $y]] \
        connected [jbool $conn] wire_net [js $net]]
}

# kind: junction | no_connect
proc ::orcadmcp::c_place_marks {spec name kind points} {
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    switch -- $kind {
        junction {set cmd PlaceJunction}
        no_connect {set cmd PlaceNoConnect}
        default {error "kind must be junction or no_connect"}
    }
    require_active $d $pg $pname
    foreach q $points {
        $cmd [gu $pg [lindex $q 0]] [gu $pg [lindex $q 1]]
    }
    catch {UnSelectAll}
    return [jobj page [js $pname] kind [js $kind] count [llength $points]]
}

# Objects whose bounding box (or wire) touches the area. Capture's area select picks exactly these
# (it selects intersecting objects, not only enclosed ones).
proc ::orcadmcp::area_objects {pg x1 y1 x2 y2} {
    set st [DboState]
    set out {}
    foreach p [inst_set $pg] {
        if {[overlaps [rect [$p GetBoundingBox]] $x1 $y1 $x2 $y2]} {
            lappend out [jobj type [js part] name [js [getstr $p GetReferenceDesignator]]]
        }
    }
    foreach {kind o sn loc rot bb} [symbols $pg] {
        if {[overlaps $bb $x1 $y1 $x2 $y2]} {
            lappend out [jobj type [js $kind] name [js $sn] location [jpt $loc]]
        }
    }
    foreach {w p1 p2 net} [page_wires $pg] {
        set xs [lsort -integer [list [lindex $p1 0] [lindex $p2 0]]]
        set ys [lsort -integer [list [lindex $p1 1] [lindex $p2 1]]]
        set r [list [lindex $xs 0] [lindex $ys 0] [lindex $xs 1] [lindex $ys 1]]
        if {[overlaps $r $x1 $y1 $x2 $y2]} {
            lappend out [jobj type [js wire] name [js $net] start [jpt $p1] end [jpt $p2]]
        }
    }
    $st -delete
    return $out
}

proc ::orcadmcp::c_delete_area {spec name x1 y1 x2 y2 apply} {
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    set hit [area_objects $pg $x1 $y1 $x2 $y2]
    if {$apply && [llength $hit]} {
        require_active $d $pg $pname
        catch {UnSelectAll}
        SelectBlock [gu $pg $x1] [gu $pg $y1] [gu $pg $x2] [gu $pg $y2] 0
        Delete
        catch {UnSelectAll}
    }
    set left [area_objects $pg $x1 $y1 $x2 $y2]
    return [jobj page [js $pname] applied [jbool $apply] objects [jarr $hit] remaining [jarr $left]]
}

# Rename the power/ground symbol or off-page connector whose bounding box contains (x y).
proc ::orcadmcp::c_rename_symbol {spec name x y newname} {
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    set hits {}
    foreach {kind o sn loc rot bb} [symbols $pg] {
        foreach {a b c e} $bb break
        if {$x >= $a && $x <= $c && $y >= $b && $y <= $e} {
            lappend hits $kind $o $sn $loc
        }
    }
    if {[llength $hits] != 4} {
        error "expected exactly one symbol at ($x $y), found [expr {[llength $hits] / 4}]"
    }
    foreach {kind o old loc} $hits break
    require_active $d $pg $pname
    catch {UnSelectAll}
    SelectObject [gu $pg $x] [gu $pg $y] 0
    set code [catch {SetProperty "Name" $newname} err]
    catch {UnSelectAll}
    if {$code} {
        error "Capture refused the rename: $err"
    }
    # Capture replaces the object when it is renamed: look it up again by kind and location
    set now ""
    foreach {k2 o2 sn2 loc2 rot2 bb2} [symbols $pg] {
        if {$k2 eq $kind && $loc2 eq $loc} {
            set now $sn2
        }
    }
    return [jobj page [js $pname] kind [js $kind] location [jpt $loc] old [js $old] new [js $now] \
        ok [jbool [string equal $now $newname]]]
}

# Clear (or set) the no-connect flag of pins in the database.
proc ::orcadmcp::c_set_no_connect {spec ref pins flag} {
    set d [design $spec]
    set found [find_parts $d $ref]
    if {[llength $found] == 0} {
        error "part '$ref' not found"
    }
    set st [DboState]
    set done {}
    foreach {sname pname p} $found {
        set it [$p NewPinsIter $st]
        set pin [$it NextPin $st]
        while {$pin ne "NULL"} {
            set num [getstr $pin GetPinNumber]
            if {[lsearch -exact $pins $num] >= 0 || [lsearch -exact $pins [getstr $pin GetPinName]] >= 0} {
                $pin SetIsNoConnect $flag
                lappend done [jobj pin [js $num] no_connect [jbool [$pin GetIsNoConnect $st]]]
            }
            set pin [$it NextPin $st]
        }
        delete_DboPartInstPinsIter $it
    }
    $st -delete
    redraw
    return [jobj refdes [js $ref] pins [jarr $done]]
}

proc ::orcadmcp::c_check_points {spec name points} {
    set d [design $spec]
    foreach {sname pname pg} [find_page $d $name] break
    set st [DboState]
    set out {}
    set wires [page_wires $pg]
    foreach q $points {
        set c [$pg IsConnectionPoint [DboTclHelper_sMakeCPoint [lindex $q 0] [lindex $q 1]] $st]
        set nets {}
        foreach {w p1 p2 net} $wires {
            if {([near $p1 $q] || [near $p2 $q]) && [lsearch -exact $nets $net] < 0} {
                lappend nets $net
            }
        }
        lappend out [jobj at [jpt $q] connection_point [jbool $c] wire_nets [jstrs $nets]]
    }
    $st -delete
    return [jobj page [js $pname] points [jarr $out]]
}

# ---------------------------------------------------------------------------------------------

proc ::orcadmcp::call {cmd args} {
    if {[info commands ::orcadmcp::c_$cmd] eq ""} {
        error "orcad-mcp: unknown command $cmd"
    }
    set code [catch {eval [linsert $args 0 ::orcadmcp::c_$cmd]} result]
    # Free the CString/int helpers created by DboTclHelper_sMake* (as Cadence's own scripts do).
    catch {DboTclHelper_sReleaseAllCreatedPtrs}
    return -code $code $result
}
