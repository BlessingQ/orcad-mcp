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

proc ::orcadmcp::call {cmd args} {
    if {[info commands ::orcadmcp::c_$cmd] eq ""} {
        error "orcad-mcp: unknown command $cmd"
    }
    set code [catch {eval [linsert $args 0 ::orcadmcp::c_$cmd]} result]
    # Free the CString/int helpers created by DboTclHelper_sMake* (as Cadence's own scripts do).
    catch {DboTclHelper_sReleaseAllCreatedPtrs}
    return -code $code $result
}
