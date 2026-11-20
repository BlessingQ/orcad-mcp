# orcad-mcp bridge for OrCAD Capture (Tcl 8.4 or later).
#
# Lets the orcad-mcp server (https://github.com/BlessingQ/orcad-mcp) run Tcl in Capture.
# Started at Capture start-up by capAutoLoad\orcad_mcp_autoload.tcl ("orcad-mcp install"),
# or by hand in the Capture Command Window:
#     source {C:/Users/<you>/.orcad-mcp/orcad_bridge.tcl}
# Stop:  ::orcadmcp::bridge::stop
#
# The bridge listens on 127.0.0.1 only. Every request must carry the token stored in
# %USERPROFILE%\.orcad-mcp\token, so only programs of the same Windows user can use it.
#
# Framing (bytes, UTF-8 payload):
#   request : "<token> <byte-length>\n<Tcl script>"
#   response: "OK <byte-length>\n<result>"   or   "ERR <byte-length>\n<error message>"
# A malformed header or a wrong token closes the connection.

namespace eval ::orcadmcp::bridge {
    variable version 1
    variable port 47800
    variable server ""
    variable token ""
    variable busy 0
    variable buf
    array set buf {}
}

proc ::orcadmcp::bridge::home {} {
    global env
    if {[info exists env(ORCAD_MCP_HOME)] && $env(ORCAD_MCP_HOME) ne ""} {
        return [file normalize $env(ORCAD_MCP_HOME)]
    }
    if {[info exists env(USERPROFILE)] && $env(USERPROFILE) ne ""} {
        return [file join [file normalize $env(USERPROFILE)] .orcad-mcp]
    }
    return [file join [file normalize ~] .orcad-mcp]
}

proc ::orcadmcp::bridge::load_token {} {
    variable token
    set dir [home]
    set f [file join $dir token]
    if {![file exists $f]} {
        # "orcad-mcp install" normally writes a strong token; this is the fallback.
        file mkdir $dir
        expr {srand(([clock clicks] ^ ([pid] << 8) ^ [clock seconds]) & 0x7fffffff)}
        set t ""
        for {set i 0} {$i < 40} {incr i} {
            append t [format %x [expr {int(rand() * 16)}]]
        }
        set fh [open $f w]
        puts -nonewline $fh $t
        close $fh
    }
    set fh [open $f r]
    set token [string trim [read $fh]]
    close $fh
    if {$token eq ""} {
        error "orcad-mcp: token file $f is empty"
    }
}

proc ::orcadmcp::bridge::start {{p ""}} {
    variable port
    variable server
    variable version
    global env
    if {$p ne ""} {
        set port $p
    } elseif {[info exists env(ORCAD_MCP_PORT)] && [string is integer -strict $env(ORCAD_MCP_PORT)]} {
        set port $env(ORCAD_MCP_PORT)
    }
    if {$server ne ""} {
        catch {close $server}
        set server ""
    }
    load_token
    if {[catch {socket -server ::orcadmcp::bridge::accept -myaddr 127.0.0.1 $port} s]} {
        catch {puts "orcad-mcp: cannot listen on 127.0.0.1:$port ($s)"}
        return ""
    }
    set server $s
    catch {puts "orcad-mcp bridge $version listening on 127.0.0.1:$port"}
    return $port
}

proc ::orcadmcp::bridge::stop {} {
    variable server
    variable buf
    if {$server ne ""} {
        catch {close $server}
        set server ""
    }
    foreach chan [array names buf] {
        drop $chan
    }
    catch {puts "orcad-mcp bridge stopped"}
}

proc ::orcadmcp::bridge::accept {chan addr cport} {
    variable buf
    if {$addr ne "127.0.0.1"} {
        catch {close $chan}
        return
    }
    fconfigure $chan -translation binary -blocking 0 -buffering full
    set buf($chan) ""
    fileevent $chan readable [list ::orcadmcp::bridge::onread $chan]
}

proc ::orcadmcp::bridge::drop {chan} {
    variable buf
    catch {close $chan}
    catch {unset buf($chan)}
}

proc ::orcadmcp::bridge::onread {chan} {
    variable buf
    variable token
    if {[catch {read $chan} data]} {
        drop $chan
        return
    }
    append buf($chan) $data
    while {1} {
        set nl [string first "\n" $buf($chan)]
        if {$nl < 0} {
            if {[string length $buf($chan)] > 256} {
                drop $chan
                return
            }
            break
        }
        set header [split [string range $buf($chan) 0 [expr {$nl - 1}]] " "]
        if {[llength $header] != 2} {
            drop $chan
            return
        }
        set tok [lindex $header 0]
        set len [lindex $header 1]
        if {![string is integer -strict $len] || $len < 0} {
            drop $chan
            return
        }
        if {$tok ne $token} {
            reply $chan ERR "orcad-mcp bridge: invalid token"
            drop $chan
            return
        }
        set end [expr {$nl + $len}]
        if {[string length $buf($chan)] <= $end} {
            break
        }
        set body [string range $buf($chan) [expr {$nl + 1}] $end]
        set buf($chan) [string range $buf($chan) [expr {$end + 1}] end]
        handle $chan $body
        if {![info exists buf($chan)]} {
            return
        }
    }
    if {[eof $chan]} {
        drop $chan
    }
}

proc ::orcadmcp::bridge::handle {chan body} {
    variable busy
    if {$busy} {
        reply $chan ERR "orcad-mcp bridge: Capture is still running another request"
        return
    }
    set busy 1
    set code [catch {uplevel #0 [encoding convertfrom utf-8 $body]} result]
    set busy 0
    if {$code == 1} {
        reply $chan ERR $result
    } else {
        reply $chan OK $result
    }
}

proc ::orcadmcp::bridge::reply {chan tag text} {
    set bytes [encoding convertto utf-8 $text]
    if {[catch {
        puts -nonewline $chan "$tag [string length $bytes]\n"
        puts -nonewline $chan $bytes
        flush $chan
    }]} {
        drop $chan
    }
}

::orcadmcp::bridge::start
