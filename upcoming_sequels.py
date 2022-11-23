"""Given an anilist username, check what shows from their completed or planning lists have known upcoming seasons."""

import argparse
from datetime import datetime

from utils import safe_post_request, depaginated_request


def get_user_id_by_name(username):
    """Given an AniList username, fetch the user's ID."""
    query_user_id = '''
query ($username: String) {
    User (name: $username) {
        id
    }
}'''

    return safe_post_request({'query': query_user_id, 'variables': {'username': username}})['User']['id']


def get_user_media(user_id, status='COMPLETED'):
    """Given an AniList user ID, fetch the user's anime list, returning a list of show IDs sorted by score (desc)."""
    query = '''
query ($userId: Int, $status: MediaListStatus, $page: Int, $perPage: Int) {
    Page (page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }
        # Note that a MediaList object is actually a single list entry, hence the need for pagination
        mediaList(userId: $userId, status: $status, sort: SCORE_DESC) {
            media {
                id
                title {
                    english
                    romaji
                }
            }
        }
    }
}'''

    return [list_entry['media'] for list_entry in depaginated_request(query=query,
                                                                      variables={'userId': user_id, 'status': status})]


def get_season_shows(season: str, season_year: int) -> list:
    """Given a season (WINTER, SPRING, SUMMER, FALL) and year, return a list of shows from that season."""
    query = '''
query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
    Page (page: $page, perPage: $perPage) {
        pageInfo {
            hasNextPage
        }
        # Note that a MediaList object is actually a single list entry, hence the need for pagination
        media(season: $season, seasonYear: $seasonYear, type: ANIME, format_in: [TV, MOVIE], sort: POPULARITY_DESC) {
            id
            title {
                english
                romaji
            }
        }
    }
}'''
    return depaginated_request(query=query, variables={'season': season, 'seasonYear': season_year})


def fuzzy_date_greater_or_equal_to(fuzzy_date, date: datetime):
    """Given a FuzzyDate as returned by anilist, return True if the date *could* be higher than the given date."""
    if fuzzy_date['day'] is not None:
        return datetime(year=fuzzy_date['year'],
                        month=fuzzy_date['month'],
                        day=fuzzy_date['day']) >= date
    elif fuzzy_date['month'] is not None:
        return fuzzy_date['year'] > date.year or (fuzzy_date['year'] == date.year and fuzzy_date['month'] >= date.month)
    elif fuzzy_date['year'] is not None:
        return fuzzy_date['year'] >= date.year

    return True


def get_related_media(show_id):
    """Given a media ID, return a generator of IDs for all airing or future anime that are direct or indirect relations of it.

    Also return their airing season and relation type.

    Optionally provide a set of media IDs to ignore (e.g. also going to be searched) to cut query count.
    """
    query = '''
query ($mediaId: Int) {
    Media(id: $mediaId) {
        relations {  # Has pageInfo but doesn't accept page args
            edges {
                relationType
                node {  # Media
                    id
                    title {
                        english
                        romaji
                    }
                    type
                    format
                    tags {
                        name  # Grabbed so we can ignore crossovers to help avoid exploding the search
                    }
                }
            }
        }
    }
}'''
    queue = {show_id}
    related_show_ids = {show_id}  # Including itself to start avoids special-casing
    while queue:
        cur_show_id = queue.pop()
        relations = safe_post_request({'query': query,
                                       'variables': {'mediaId': cur_show_id}})['Media']['relations']['edges']
        for relation in relations:
            show = relation['node']
            # Manga don't need to be included in the output and ignoring them trims our search queries way down
            if show['id'] not in related_show_ids:
                related_show_ids.add(show['id'])
                if show['id'] != show_id:
                    yield show

                # Only chain through a few relation types to keep the search small
                if (relation['relationType'] not in {'SEQUEL', 'PREQUEL', 'SOURCE', 'ALTERNATIVE'}
                        or any(tag['name'] == 'Crossover' for tag in show['tags'])):
                    continue

                queue.add(show['id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Given an anilist username, check what shows from their completed or planning lists have known\n"
                    "upcoming seasons.",
        formatter_class=argparse.RawTextHelpFormatter)  # Preserves newlines in help text
    parser.add_argument('username', help="User whose list should be checked.")
    parser.add_argument('-p', '--planning', action='store_true',
                        help="Check only for sequels of shows in the user's planning list.")
    parser.add_argument('-c', '--completed', action='store_true',
                        help="Check only for sequels of shows in the user's completed list.")
    args = parser.parse_args()

    user_id = get_user_id_by_name(args.username)

    # Fetch the user's relevant media lists (anime or manga)
    user_media_ids_by_status = {status: set(media['id'] for media in get_user_media(user_id, status))
                                for status in ('COMPLETED', 'PLANNING', 'CURRENT')}
    user_media_ids = set().union(*user_media_ids_by_status.values())

    # Search the current and next 3 seasons (4 total)
    cur_date = datetime.utcnow()
    for i in range(4):
        season_idx = (3 * i + cur_date.month - 1) // 3
        season = ['WINTER', 'SPRING', 'SUMMER', 'FALL'][season_idx % 4]
        year = cur_date.year + season_idx // 4

        if i != 0:
            print("")
        print(f"{season} {year}")
        print("=" * 40)

        season_shows = get_season_shows(season=season, season_year=year)

        # Search each of the shows' relations for a show in the user's list
        for show in season_shows:
            if any(related_media['id'] in user_media_ids for related_media in get_related_media(show['id'])):
                print(show['title']['english'] or show['title']['romaji'])

    print(f"\nTotal queries: {safe_post_request.total_queries}")
