import { LiveClient, type Op } from "./live/client";

const usage = `usage:
  aae '<op json>' ['<op json>' ...]   run ops as one batch
  aae events                         stream Live events`;

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error(usage);
  process.exit(2);
}

const live = await new LiveClient().connect();
if (args[0] === "events") {
  live.onEvent = (e) => console.log(JSON.stringify(e));
  await live.subscribe();
} else {
  const ops: Op[] = args.map((a) => JSON.parse(a));
  const res = await live.batch(ops);
  console.log(JSON.stringify(res, null, 2));
  live.close();
  process.exit(res.ok ? 0 : 1);
}
