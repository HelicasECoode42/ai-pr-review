def load_for_actor(account_id, actor):
    account = accounts.get(account_id)
    if account.owner_id != actor.id:
        raise NotFound()
    return account
