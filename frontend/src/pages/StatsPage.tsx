import { useState } from 'react';
import { IconSearch, IconClose } from '../components/icons';
import { SkeletonTable } from '../components/Skeleton';
import { PlayerTable } from '../components/player/PlayerTable';
import { TeamTable } from '../components/TeamTable';
import { PlayerModal } from '../components/player/PlayerModal';
import { CompareModal } from '../components/player/CompareModal';
import { SegmentedFilter } from '../components/SegmentedFilter';
import { getPlayers, getTeams } from '../api/client';
import { useCachedResource } from '../hooks/useCachedResource';
import { CACHE_KEYS } from '../api/resourceCache';
import type { Player, Team } from '../types';

const POSITIONS = ['All', 'PG', 'SG', 'SF', 'PF', 'C'];

export const StatsPage = () => {
  const [view, setView] = useState<'players' | 'teams'>('players');
  const [selectedPlayer, setSelectedPlayer] = useState<Player | null>(null);
  const [search, setSearch] = useState('');
  const [teamFilter, setTeamFilter] = useState('');
  const [posFilter, setPosFilter] = useState('All');
  const [comparePlayers, setComparePlayers] = useState<Player[]>([]);
  const [showCompare, setShowCompare] = useState(false);
  const [confFilter, setConfFilter] = useState<'All' | 'East' | 'West'>('All');

  const { data: playersData, loading: loadingPlayers } = useCachedResource<Player[]>(
    CACHE_KEYS.players,
    () => getPlayers()
  );
  const { data: teamsData, loading: loadingTeams } = useCachedResource<Team[]>(
    CACHE_KEYS.teams,
    getTeams
  );
  const players = playersData ?? [];
  const teams = teamsData ?? [];

  const teamNames = [...new Set(players.map((p) => p.team))].sort();

  const filteredPlayers = players.filter((p) => {
    const matchesSearch = !search || p.name.toLowerCase().includes(search.toLowerCase());
    const matchesTeam = !teamFilter || p.team === teamFilter;
    const matchesPos = posFilter === 'All' || p.position.split(',').includes(posFilter);
    return matchesSearch && matchesTeam && matchesPos;
  });

  const handleToggleCompare = (player: Player): void => {
    setComparePlayers((prev) => {
      const exists = prev.find((p) => p.id === player.id);
      if (exists) return prev.filter((p) => p.id !== player.id);
      if (prev.length >= 3) return prev;
      return [...prev, player];
    });
  };

  return (
    <div className="pb-20">
      <div className="max-w-[1400px] mx-auto px-4 py-6">
        <div className="flex items-center gap-4 mb-5">
          <div className="tabs tabs-boxed">
            <button
              onClick={() => setView('players')}
              className={`tab ${view === 'players' ? 'tab-active' : ''}`}
            >
              Players
            </button>
            <button
              onClick={() => setView('teams')}
              className={`tab ${view === 'teams' ? 'tab-active' : ''}`}
            >
              Teams
            </button>
          </div>

          {view === 'players' && (
            <span className="text-sm text-faint">
              {filteredPlayers.length} player{filteredPlayers.length !== 1 ? 's' : ''}
            </span>
          )}

          {view === 'teams' && (
            <SegmentedFilter
              options={(['All', 'East', 'West'] as const).map((conf) => ({ value: conf, label: conf }))}
              value={confFilter}
              onChange={setConfFilter}
              ariaLabel="Conference"
            />
          )}
        </div>

        {view === 'players' && (
          <div className="flex flex-wrap items-center gap-3 mb-5">
            <label className="input input-bordered input-sm flex items-center gap-2 flex-1 min-w-[200px] max-w-[360px]">
              <IconSearch size={14} className="text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search players..."
                className="grow"
              />
            </label>

            <select
              value={teamFilter}
              onChange={(e) => setTeamFilter(e.target.value)}
              className="select select-bordered select-sm w-[160px]"
              aria-label="Filter by team"
            >
              <option value="">All Teams</option>
              {teamNames.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>

            <SegmentedFilter
              options={POSITIONS.map((pos) => ({ value: pos, label: pos }))}
              value={posFilter}
              onChange={setPosFilter}
              ariaLabel="Position"
            />
          </div>
        )}

        {view === 'players' ? (
          loadingPlayers ? (
            <SkeletonTable rows={12} cols={10} label="Loading players" />
          ) : (
            <PlayerTable
              players={filteredPlayers}
              onSelect={(p) => setSelectedPlayer(p)}
              selectedForCompare={comparePlayers}
              onToggleCompare={handleToggleCompare}
            />
          )
        ) : loadingTeams ? (
          <SkeletonTable rows={12} cols={10} label="Loading teams" />
        ) : (
          <TeamTable
            teams={confFilter === 'All' ? teams : teams.filter((t) => t.conference?.toLowerCase().startsWith(confFilter.toLowerCase()))}
          />
        )}
      </div>

      {comparePlayers.length >= 1 && (
        <div className="fixed bottom-0 left-0 right-0 z-40 bg-base-200 border-t border-base-300 px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted">{comparePlayers.length}/3 selected</span>
            <div className="flex items-center gap-2">
              {comparePlayers.map((p) => (
                <div key={p.id} className="flex items-center gap-1.5 bg-base-300 rounded-box pl-1 pr-2 py-1">
                  <div className="avatar">
                    <div className="w-5 rounded-box">
                      <img
                        src={p.headshot_url || ''}
                        alt=""
                        onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                      />
                    </div>
                  </div>
                  <span className="text-xs">{p.name.split(' ').pop()}</span>
                  <button
                    onClick={() => handleToggleCompare(p)}
                    className="ml-0.5 text-faint hover:text-base-content"
                    aria-label={`Remove ${p.name} from comparison`}
                  >
                    <IconClose size={10} />
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setComparePlayers([])} className="btn btn-ghost btn-sm">
              Clear
            </button>
            <button
              onClick={() => setShowCompare(true)}
              disabled={comparePlayers.length < 2}
              className="btn btn-primary btn-sm"
            >
              Compare {comparePlayers.length >= 2 ? comparePlayers.length : ''} Players
            </button>
          </div>
        </div>
      )}

      <PlayerModal player={selectedPlayer} onClose={() => setSelectedPlayer(null)} />
      {showCompare && (
        <CompareModal players={comparePlayers} onClose={() => setShowCompare(false)} />
      )}
    </div>
  );
};
