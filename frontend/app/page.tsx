/**
 * The only route. Amon Hen is a single operational screen, not a site.
 *
 * Everything is composed inside `OperationsView`, which is a client component;
 * this stays a server component so the shell renders without waiting on any of
 * the map libraries.
 */
import { OperationsView } from "@/components/OperationsView";

export default function Page() {
  return <OperationsView />;
}
