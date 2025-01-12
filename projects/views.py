from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from .models import Projects
import json
import hashlib
from django.core.files import File
import os
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Image
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Q
import time
import datetime as dt

def is_2fa_authenticated(user):
    try:
        return user.is_authenticated and user.is_two_factor_enabled is True and len(user.totpdevice_set.all())>0
    except user.DoesNotExist:
        return False


def load_json_file(level):
    results = []
    with open('common/asvs.json') as f:
        data = json.load(f)
        for r in data['requirements']:
            bob = 'L{0}'.format(level)
            if r.get(bob):
                results.append(r)
    return results


def create_template(requirements, project):
    # delete all the levels out of the requirements as not needed in the template
    # for r in requirements:
    #     del r['levels']
    #     r['enabled'] = 0
    # build the template with project information and requirements
    data = {}
    data['project_owner'] = project['project_owner']
    data['project_name'] = project['project_name']
    data['project_id'] = project['id']
    data['project_description'] = project['project_description']
    data['project_created'] = project['project_created'].isoformat()
    data['project_level'] = project['project_level']
    data['requirements'] = requirements
    data['project_allowed_viewers'] = project['project_owner']
    phash = (hashlib.sha3_256('{0}{1}'.format(
        project['project_owner'], project['id']).encode('utf-8')).hexdigest())
    with open('storage/{0}.json'.format(phash), 'w') as output:
        project_file = File(output)
        json.dump(data, project_file, indent=2)
    project_file.close()
    return


def load_template(phash):
    with open('storage/{0}.json'.format(phash), 'r') as template:
        data = json.load(template)
        template.close()
        return data


def update_template(phash, data):
    with open('storage/{0}.json'.format(phash), 'w') as template:
        json.dump(data, template, indent=2)
    template.close()
    return


def calculate_completion(requirements):
    total = len(requirements)
    enabled = 0
    for r in requirements:
        if r.get('enabled') and r['enabled'] > 0:
            enabled += 1
        else:
            pass
    percentage = enabled / total * 100
    return {'total': total, 'enabled': enabled, 'percentage': '{0:.1f}'.format(percentage)}


@user_passes_test(is_2fa_authenticated, login_url='/home')
def project_all(request):
    if is_2fa_authenticated(request.user):
        if request.user.is_superuser:
            projects = Projects.objects.all().values()
        else:
            projects = Projects.objects.filter(Q(project_owner=request.user.username) | Q(
                project_allowed_viewers__icontains=request.user.username)).values()
        return render(request, 'projects/manage.html', {'projects': projects, 'user': request.user})


@user_passes_test(is_2fa_authenticated)
def project_add(request):
    if request.method == 'POST':
        # Create the database record
        project_name = request.POST.get('project_name')
        project_owner = request.user.username
        project_description = request.POST.get('project_description')
        project_level = request.POST.get('project_level')
        p = Projects(project_name=project_name, project_owner=project_owner,
                     project_description=project_description, project_level=project_level, project_allowed_viewers=project_owner)
        p.save()
        # Build the template
        controls = load_json_file(project_level)
        project = Projects.objects.filter(
            project_owner=request.user.username, project_name=project_name).values()[0]
        create_template(controls, project)
        return redirect('projectsmanage')


@user_passes_test(is_2fa_authenticated)
def project_delete(request, projectid):
    Projects.objects.filter(
        project_owner=request.user.username, pk=projectid).delete()
    phash = (hashlib.sha3_256('{0}{1}'.format(
        request.user.username, projectid).encode('utf-8')).hexdigest())
    os.remove('storage/{0}.json'.format(phash))
    return redirect('projectsmanage')


@user_passes_test(is_2fa_authenticated)
def project_view(request, projectid):
    phash = (hashlib.sha3_256('{0}{1}'.format(
        request.user.username, projectid).encode('utf-8')).hexdigest())
    project = load_template(phash)
    allowed_users = project['project_allowed_viewers'].split(",")
    project['project_created']=  add_one_hour(time.strftime("%m/%d/%Y %H:%M:%S",time.strptime(project['project_created'][:19], "%Y-%m-%dT%H:%M:%S")))
    if project['project_owner'] == request.user.username or request.user.username in allowed_users:
        percentage = calculate_completion(project['requirements'])

        return render(request, "projects/view.html", {'data': project['requirements'], 'project': project, 'percentage': percentage})


@user_passes_test(is_2fa_authenticated)
def project_update(request):
    if request.method == 'POST':
        phash = (hashlib.sha3_256('{0}{1}'.format(request.user.username, request.POST.get(
            'projectid')).encode('utf-8')).hexdigest())
        project = load_template(phash)
        for k, v in request.POST.items():
            if 'csrfmiddlewaretoken' in k or 'projectid' in k:
                pass
            else:
                for r in project['requirements']:
                    if request.POST.get(r['Item']+'enabled') == "1":
                        r['enabled'] = 1
                    else:
                        r['enabled'] = 0
                    if request.POST.get(r['Item']+'disabled') == "1":
                        r['disabled'] = 1
                    else:
                        r['disabled'] = 0
                    if request.POST.get(r['Item']+'na') == "1":
                        r['enabled'] = 0
                        r['disabled'] = 0

        update_template(phash, project)
        return redirect('projectsview', projectid=request.POST.get('projectid'))


@user_passes_test(is_2fa_authenticated)
def project_download(request, projectid):
    phash = (hashlib.sha3_256('{0}{1}'.format(
        request.user.username, projectid).encode('utf-8')).hexdigest())
    filename = 'storage/{0}.json'.format(phash)
    with open(filename, 'rb') as fh:
        response = HttpResponse(
            fh.read(), content_type="application/json")
        response['Content-Disposition'] = 'inline; filename=' + \
            os.path.basename(filename)
        return response


@user_passes_test(is_2fa_authenticated)
def generate_pdf(request, projectid):
    phash = (hashlib.sha3_256('{0}{1}'.format(
        request.user.username, projectid).encode('utf-8')).hexdigest())
    project = load_template(phash)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="ProjectReport.pdf"'
    buffer = BytesIO()
    p = canvas.Canvas(buffer)
    data = [['PROJECT REPORT']]
    # Create the PDF object, using the BytesIO object as its "file."

    data.append(["Project Owner:"])
    data.append([str(project['project_owner'])])
    data.append(["Project Name:"])
    data.append([str(project['project_name'])])
    data.append(["Project ID:"])
    data.append([str(project['project_id'])])
    data.append(["Project Description:"])
    data.append([str(project['project_description'])])
    data.append(["Project Created:"])
    data.append([str(project['project_created'])])
    data.append(["Project Level:"])
    data.append([str(project['project_level'])])
    data.append([" "])
    data.append(["Requirements"])
    data.append([" "])
    for r in project['requirements']:
        data.append([r['Name']+":"])
        data.append([" "])
        split_description = chunkstring(r['Description'], 123)
        for sd in split_description:
            data.append([sd])
        if r.get('enabled') and r['enabled'] > 0:
            data.append(["Complete ✓"])
            data.append([" "])

        elif r.get('disabled') and r['disabled'] > 0:
            data.append(["Incomplete ✕"])
            data.append([" "])
        else:
            data.append(["N/A "])
            data.append([" "])

    if len(data) >= 40:
        for x in range(len(data)+1):
            if (((x % 40 == 0) and (x > 0)) or x == len(data)):
                smalldata = data[x-40:x]
                width = 800
                height = 200
                x = 20
                y = 80
                f = Table(smalldata)
                f.wrapOn(p, width, height)
                f.drawOn(p, x, y)
                p.showPage()

    else:
        width = 800
        height = 200
        x = 20
        y = 767-17*len(data)
        f = Table(data)
        f.wrapOn(p, width, height)
        f.drawOn(p, x, y)
        p.showPage()

    p.save()

    # Get the value of the BytesIO buffer and write it to the response.
    pdf = buffer.getvalue()
    buffer.close()
    response.write(pdf)
    return response


def chunkstring(text, length):

    list_of_strings = []
    for i in range(0, len(text), length):
        list_of_strings.append(text[i:length+i])
    return(list_of_strings)


def modify_allowed_users(request, projectid):
    if request.method == 'POST':
        phash = (hashlib.sha3_256('{0}{1}'.format(
            request.user.username, projectid).encode('utf-8')).hexdigest())

        change = Projects.objects.get(id=projectid)
        change.project_allowed_viewers = request.POST.get('viewers')
        change.save()
        return redirect('projectsmanage')

#Adjust UTC timestamp to "Europe/London" Timezone
def add_one_hour(time_string):
    the_time = dt.datetime.strptime(time_string, '%m/%d/%Y %H:%M:%S')
    new_time = the_time + dt.timedelta(hours=1)
    return new_time.strftime('%m/%d/%Y %H:%M:%S')