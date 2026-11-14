import { Suspense } from "react";

import { AssetView } from "./asset-view";

// The asset id is only known at request time, so reading it has to sit inside Suspense.
export default function AssetPage({ params }: PageProps<"/catalog/[id]">) {
  return (
    <Suspense>
      <AssetView params={params} />
    </Suspense>
  );
}
