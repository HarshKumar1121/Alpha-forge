/**
 * Data Explorer (spec §4.2): a synced market's fields, narrowed in More Filters down to whole
 * categories, whole subcategories or single datasets. Syncing lives in BRAIN › Sync.
 */

import { useEffect, useState } from 'react'
import { useScope } from '@/lib/scope'
import { useDatasetPick } from '@/screens/data/dataset-pick'
// forge: the Datasets tab lives in screens/forge/data/; this screen only switches to it.
import { DatasetsTab } from '@/screens/forge/data/datasets-tab'
import { Page, PageHeader, Segmented } from '@/ui/kit'
import { FieldsTab } from './fields'
import { MarketBar } from './market'

export function DataScreen() {
  const [scope, update] = useScope('data')
  const picking = useDatasetPick((s) => s.active)
  // forge: Fields is the upstream tab; Datasets is Forge's view of the same market.
  const [tab, setTab] = useState<'fields' | 'datasets'>('fields')

  // A pick follows the market shown here.
  useEffect(() => {
    if (picking) useDatasetPick.getState().follow(scope)
  }, [picking, scope])

  return (
    <Page>
      <PageHeader
        title="Data Explorer"
        description="Browse, filter and compare the Data Fields you synced from BRAIN, locally."
        actions={
          // forge: field-level browsing, or dataset-level with crowding and your own history.
          !picking && (
            <Segmented
              label="View"
              value={tab}
              onChange={setTab}
              items={[
                { value: 'fields', label: 'Fields' },
                { value: 'datasets', label: 'Datasets' },
              ]}
            />
          )
        }
      />
      <MarketBar scope={scope} update={update} />
      {/* forge: the Datasets tab; Fields is upstream's and is untouched. */}
      {tab === 'fields' || picking ? <FieldsTab scope={scope} /> : <DatasetsTab scope={scope} />}
    </Page>
  )
}
