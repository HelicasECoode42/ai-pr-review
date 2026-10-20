from accounts import load_for_actor

def account_page(account_id, actor):
    account = accounts.get(account_id)
    if account.owner_id != actor.id:
        raise NotFound()
    return render(account)
