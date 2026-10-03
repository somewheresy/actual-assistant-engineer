import { LiveClient, type Op } from "./live/client";

const usage = `usage:
  assistant-engineer '<op json>' ['<op json>' ...]   run ops as one batch
  assistant-engineer events                         stream Live events
  assistant-engineer reload                         reload control surface code (development)
  assistant-engineer review [--require a,b,...]     completeness gate: prints gaps, exits 1 until none
                                     (arrangement, locators, automation, sidechain, mix)`;

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error(usage);
  process.exit(2);
}

const live = await new LiveClient().connect();
if (args[0] === "review") {
  const flag = args.indexOf("--require");
  const require = Object.fromEntries((flag >= 0 ? args[flag + 1] ?? "" : "").split(",").filter(Boolean).map((k) => [k, true]));
  const r = await live.run<{ ok: boolean; complete: boolean; gaps: string[]; arrangement: { bars: number; locators: { name: string }[] } }>({ op: "review", require });
  live.close();
  console.log(r.complete ? `complete: ${r.arrangement.bars} bars, sections ${r.arrangement.locators.map((l) => l.name).join(" > ")}` : `gaps:\n- ${r.gaps.join("\n- ")}`);
  process.exit(r.complete ? 0 : 1);
} else if (args[0] === "reload") {
  console.log(JSON.stringify(await live.reload()));
  live.close();
} else if (args[0] === "events") {
  live.onEvent = (e) => console.log(JSON.stringify(e));
  await live.subscribe();
} else {
  const ops: Op[] = args.map((a) => JSON.parse(a));
  const res = await live.batch(ops);
  console.log(JSON.stringify(res, null, 2));
  live.close();
  process.exit(res.ok ? 0 : 1);
}
