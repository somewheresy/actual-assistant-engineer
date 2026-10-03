// hermes-midi: publishes a CoreMIDI virtual source ("Hermes Performance") and
// forwards MIDI read from stdin, one message per line as hex bytes ("b0 07 64").
// Live receives it on the Hermes control surface's input port.
import CoreMIDI
import Foundation

let name = CommandLine.arguments.dropFirst().first ?? "Hermes Performance"

var client = MIDIClientRef()
var source = MIDIEndpointRef()
func check(_ status: OSStatus, _ what: String) {
    if status != noErr {
        FileHandle.standardError.write("hermes-midi: \(what) failed (\(status))\n".data(using: .utf8)!)
        exit(1)
    }
}
check(MIDIClientCreate(name as CFString, nil, nil, &client), "MIDIClientCreate")
check(MIDISourceCreate(client, name as CFString, &source), "MIDISourceCreate")

func send(_ bytes: [UInt8]) {
    var packets = MIDIPacketList()
    let packet = MIDIPacketListInit(&packets)
    _ = MIDIPacketListAdd(&packets, MemoryLayout<MIDIPacketList>.size, packet, 0, bytes.count, bytes)
    MIDIReceived(source, &packets)
}

print("ready \(name)")
fflush(stdout)
while let line = readLine() {
    let bytes = line.split(separator: " ").compactMap { UInt8($0, radix: 16) }
    if bytes.isEmpty { continue }
    send(bytes)
    print("sent \(String(format: "%.6f", Date().timeIntervalSince1970))")
    fflush(stdout)
}
