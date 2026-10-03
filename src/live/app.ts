// Use the shipped platform-aware discovery, including AAE_LIVE_PATH.
import { pythonJson } from "./python";

/** Live's .app on macOS, executable on Windows. */
export const liveBundle = (): string => pythonJson({ action: "bundle" });
export const defaultSet = (): string => pythonJson({ action: "default_set" });
