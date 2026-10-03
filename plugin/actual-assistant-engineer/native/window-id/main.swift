// window-id <owner>: print the CGWindowID of the owner's largest on-screen window (for `screencapture -l`).
import CoreGraphics
import Foundation

let owner = CommandLine.arguments.dropFirst().first ?? "Live"
let windows = (CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]]) ?? []
let best = windows
    .filter { ($0[kCGWindowOwnerName as String] as? String) == owner && ($0[kCGWindowLayer as String] as? Int) == 0 }
    .max { a, b in
        let area = { (w: [String: Any]) -> Double in
            let r = w[kCGWindowBounds as String] as? [String: Double] ?? [:]
            return (r["Width"] ?? 0) * (r["Height"] ?? 0)
        }
        return area(a) < area(b)
    }
guard let id = best?[kCGWindowNumber as String] as? Int else { exit(1) }
print(id)
